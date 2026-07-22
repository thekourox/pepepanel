import os
import json
import uuid
import urllib.request
import httpx
import math
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from contextlib import asynccontextmanager
import wireproxy_manager
import threading

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    def _recovery_thread():
        try:
            print("Auto-recovering wireproxies on startup...")
            wireproxy_manager.recover_all_proxies()
        except Exception as e:
            print(f"Failed to auto-recover wireproxies: {e}")
            
    # Run recovery in background so it doesn't block FastAPI startup
    threading.Thread(target=_recovery_thread, daemon=True).start()
    
    watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True)
    watchdog_thread.start()
    print("Surfshark Watchdog started (checking every 2 minutes).")
    
    yield
    
    # Shutdown
    print("Shutting down... killing all wireproxy instances gracefully.")
    wireproxy_manager._kill_all_wireproxy()

app = FastAPI(title="Pasargard VPN Automator", lifespan=lifespan)

static_dir = os.path.join(os.path.dirname(__file__), "static")
templates_dir = os.path.join(os.path.dirname(__file__), "templates")

os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

class Location(BaseModel):
    country: str
    countryCode: str
    location: str
    endpoint: str
    publicKey: str

class KeyPair(BaseModel):
    private_key: str
    wg_address: str

class InjectRequest(BaseModel):
    key_pairs: Optional[List[KeyPair]] = None
    private_key: Optional[str] = None
    wg_address: Optional[str] = None
    core_id: str
    template_inbound_id: str
    locations: List[Location]
    server_ip: str = "127.0.0.1"

class GroupCreateRequest(BaseModel):
    group_name: str
    core_id: str
    locations: List[Location]

class LifecycleToggleRequest(BaseModel):
    core_id: str
    enable: bool

class LifecycleCleanupRequest(BaseModel):
    core_id: str

class RestoreRequest(BaseModel):
    key_pairs: List[KeyPair]
    core_data: dict

INJECTION_STATE_FILE = os.path.join(os.path.dirname(__file__), "injection_state.json")

def load_injection_state():
    if os.path.exists(INJECTION_STATE_FILE):
        try:
            with open(INJECTION_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def save_injection_state(location_count, port_map):
    import datetime
    state = {
        "injected": True,
        "location_count": location_count,
        "injected_at": datetime.datetime.now().isoformat(),
        "port_map": port_map  # {socks_port: tag}
    }
    with open(INJECTION_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

@app.get("/", response_class=HTMLResponse)
async def serve_gui(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/injection/status")
def get_injection_status():
    """Check if injection was already done and return proxy health stats."""
    state = load_injection_state()
    active_proxies = wireproxy_manager.get_active_proxies()
    
    alive = 0
    dead = 0
    for tag, port in active_proxies.items():
        if wireproxy_manager.health_check_proxy(port, timeout=2.0):
            alive += 1
        else:
            dead += 1
    
    return {
        "injected": state is not None and state.get("injected", False),
        "location_count": state.get("location_count", 0) if state else 0,
        "injected_at": state.get("injected_at", "") if state else "",
        "total_configs": len(active_proxies),
        "alive": alive,
        "dead": dead
    }

@app.get("/api/surfshark/locations")
def get_surfshark_locations():
    """Fetch all 110+ live locations directly from Surfshark's public API."""
    try:
        req = urllib.request.Request(
            'https://api.surfshark.com/v4/server/clusters',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            
        locations = []
        for cluster in data:
            if 'pubKey' in cluster and 'connectionName' in cluster:
                locations.append({
                    "country": cluster.get("country", "Unknown"),
                    "countryCode": cluster.get("countryCode", "US"),
                    "location": cluster.get("location", "Unknown"),
                    "endpoint": cluster.get("connectionName"),
                    "publicKey": cluster.get("pubKey")
                })
        locations.sort(key=lambda x: x['country'])
        return locations
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch Surfshark locations: {str(e)}")

@app.get("/api/pasargard/cores")
async def get_cores(
    authorization: str = Header(None),
    x_pasarguard_host: str = Header(None)
):
    if not authorization or not x_pasarguard_host:
        raise HTTPException(status_code=401, detail="Missing PasarGuard credentials in headers.")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{x_pasarguard_host.rstrip('/')}/api/cores/simple",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                timeout=120.0
            )
            resp.raise_for_status()
            data = resp.json()
            cores = [{"id": str(c["id"]), "setting_key": c["name"]} for c in data.get("cores", [])]
            return cores
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PasarGuard API Error: {str(e)}")

@app.get("/api/pasargard/inbounds")
async def get_inbounds(
    core_id: str,
    authorization: str = Header(None),
    x_pasarguard_host: str = Header(None)
):
    if not authorization or not x_pasarguard_host:
        raise HTTPException(status_code=401, detail="Missing PasarGuard credentials in headers.")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{x_pasarguard_host.rstrip('/')}/api/hosts",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                timeout=120.0
            )
            resp.raise_for_status()
            data = resp.json()
            
            inbounds = []
            for host in data:
                inbounds.append({
                    "id": str(host["id"]),
                    "remark": host.get("remark", f"Host {host['id']}"),
                    "port": host.get("port", 0)
                })
            return inbounds
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PasarGuard API Error: {str(e)}")

@app.post("/api/pasargard/inject")
async def inject_to_pasargard(
    request: InjectRequest,
    authorization: str = Header(None),
    x_pasarguard_host: str = Header(None)
):
    if not authorization or not x_pasarguard_host:
        raise HTTPException(status_code=401, detail="Missing PasarGuard credentials in headers.")
    
    try:
        async with httpx.AsyncClient() as client:
            # 1. Fetch Core Config
            core_resp = await client.get(
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                timeout=120.0
            )
            core_resp.raise_for_status()
            core_data = core_resp.json()
            xray_config = core_data["config"]
            
            if "inbounds" not in xray_config:
                xray_config["inbounds"] = []
            if "outbounds" not in xray_config:
                xray_config["outbounds"] = []
            if "routing" not in xray_config:
                xray_config["routing"] = {"rules": []}
            if "rules" not in xray_config["routing"]:
                xray_config["routing"]["rules"] = []
                
            # STRIP PREVIOUS INJECTIONS TO PREVENT DUPLICATES
            xray_config["inbounds"] = [i for i in xray_config.get("inbounds", []) if not (i.get("tag", "").startswith("Surf-") or i.get("tag", "").startswith("B-In-"))]
            xray_config["outbounds"] = [o for o in xray_config.get("outbounds", []) if not (o.get("tag", "").startswith("SurfOut-") or o.get("tag", "").startswith("B-Out-"))]
            xray_config["routing"]["rules"] = [
                r for r in xray_config["routing"]["rules"] 
                if not (
                    (isinstance(r.get("inboundTag"), list) and any(t.startswith("Surf-") or t.startswith("B-In-") for t in r.get("inboundTag"))) or 
                    (isinstance(r.get("outboundTag"), str) and (r.get("outboundTag", "").startswith("SurfOut-") or r.get("outboundTag", "").startswith("B-Out-")))
                )
            ]
            
            # 1. Fetch existing hosts to preserve group assignments
            existing_hosts = []
            try:
                hosts_resp = await client.get(
                    f"{x_pasarguard_host.rstrip('/')}/api/hosts",
                    headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                    timeout=120.0
                )
                if hosts_resp.status_code == 200:
                    for old_h in hosts_resp.json():
                        tag = old_h.get("inbound_tag", "")
                        if tag.startswith("Surf-") or tag.startswith("B-In-"):
                            existing_hosts.append(old_h)
            except Exception as e:
                pass
                
            # 2. Fetch Template Host
            host_resp = await client.get(
                f"{x_pasarguard_host.rstrip('/')}/api/host/{request.template_inbound_id}",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                timeout=120.0
            )
            host_resp.raise_for_status()
            template_host = host_resp.json()
            
            template_inbound_tag = template_host.get("inbound_tag")
            if not template_inbound_tag:
                raise HTTPException(status_code=400, detail="Template host has no inbound_tag.")
                
            template_inbound = next((inb for inb in xray_config["inbounds"] if inb.get("tag") == template_inbound_tag), None)
            if not template_inbound:
                raise HTTPException(status_code=400, detail="Template inbound tag not found in core config.")
            
            # Find max port used in core to assign new ports
            used_ports = {inb.get("port") for inb in xray_config["inbounds"] if isinstance(inb.get("port"), int)}
            next_port = max(used_ports) + 1 if used_ports else 10000
            
            # 2. Extract Key Pairs
            import math
            import re
            key_pairs = getattr(request, 'key_pairs', None)
            if not key_pairs:
                if request.private_key and request.wg_address:
                    key_pairs = [{"private_key": request.private_key, "wg_address": request.wg_address}]
                else:
                    raise HTTPException(status_code=400, detail="No valid Surfshark Key Pairs provided.")
            
            num_keys = len(key_pairs)
            if num_keys == 0:
                raise HTTPException(status_code=400, detail="Key pairs list is empty.")
                
            locations_per_key = math.ceil(len(request.locations) / num_keys)

            # 3. Process each location
            used_socks_ports = [
                server.get("port") 
                for ob in xray_config["outbounds"] 
                if ob.get("protocol") == "socks" 
                for server in ob.get("settings", {}).get("servers", [])
            ]
            used_socks_ports = [p for p in used_socks_ports if isinstance(p, int)]
            local_port = max(used_socks_ports) + 1 if used_socks_ports and max(used_socks_ports) >= 20000 else 20000
            
            host_payloads = []
            
            # Wipe all existing wireproxy processes to ensure a clean slate and prevent port conflicts
            wireproxy_manager._kill_all_wireproxy()
            import time
            time.sleep(1.0) # Wait for UDP sockets to be released
            
            for i, loc in enumerate(request.locations):
                # Calculate which Key Pair to use for this location
                current_key_idx = i // locations_per_key
                current_kp = key_pairs[current_key_idx]
                kp_private_key = current_kp["private_key"] if isinstance(current_kp, dict) else current_kp.private_key
                kp_wg_address = current_kp["wg_address"] if isinstance(current_kp, dict) else current_kp.wg_address
                
                clean_wg_address = kp_wg_address.split('/')[0] + "/32"
                safe_country = re.sub(r'[^A-Za-z0-9]', '', loc.country)
                
                # Compute expected remark to match with existing host
                def get_flag(cc: str) -> str:
                    if not cc or len(cc) != 2: return ""
                    return chr(ord(cc[0].upper()) + 127397) + chr(ord(cc[1].upper()) + 127397)
                
                flag = get_flag(loc.countryCode)
                location_part = f" - {loc.location}" if loc.location and loc.location.strip() != loc.country.strip() else ""
                expected_remark = f"{loc.country}{location_part} {flag}"
                
                # Match with existing host
                matched_host = None
                for i, h in enumerate(existing_hosts):
                    if h.get("remark") == expected_remark:
                        matched_host = existing_hosts.pop(i)
                        break
                        
                import random, string, uuid
                
                if matched_host:
                    cloned_tag = matched_host.get("inbound_tag")
                    parts = cloned_tag.split("-")
                    suffix = parts[3] if len(parts) >= 4 else ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
                    outbound_tag = f"B-Out-{safe_country}-{suffix}"
                    new_uuid = matched_host.get("uuid", str(uuid.uuid4()))
                    this_port = matched_host.get("port")
                    is_new = False
                else:
                    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
                    outbound_tag = f"B-Out-{safe_country}-{suffix}"
                    cloned_tag = f"B-In-{safe_country}-{suffix}"
                    new_uuid = str(uuid.uuid4())
                    this_port = next_port
                    next_port += 1
                    is_new = True
                # (Legacy stop method removed since we now kill all processes upfront)
                
                # Start wireproxy SOCKS5 process locally
                wireproxy_manager.start_wireproxy(
                    tag=outbound_tag,
                    private_key=kp_private_key,
                    wg_address=clean_wg_address,
                    endpoint=f"{loc.endpoint}:51820",
                    public_key=loc.publicKey,
                    local_port=local_port
                )
                
                # Clone Inbound
                import copy
                new_inbound = copy.deepcopy(template_inbound)
                new_inbound["tag"] = cloned_tag
                new_inbound["port"] = this_port
                xray_config["inbounds"].append(new_inbound)
                
                # Auto-open firewall port
                os.system(f"ufw allow {this_port}/tcp >/dev/null 2>&1")
                
                # Update Core Xray Config with SOCKS Outbound
                xray_config["outbounds"] = [ob for ob in xray_config["outbounds"] if ob.get("tag") != outbound_tag]
                socks_outbound = {
                    "tag": outbound_tag,
                    "protocol": "socks",
                    "settings": {
                        "servers": [
                            {
                                "address": "127.0.0.1",
                                "port": local_port
                            }
                        ]
                    }
                }
                xray_config.setdefault("outbounds", []).append(socks_outbound)
                
                # Increment ports for the next location in the loop
                local_port += 1
                
                xray_config["routing"]["rules"] = [r for r in xray_config["routing"]["rules"] if r.get("outboundTag") != outbound_tag]
                new_rule = {
                    "type": "field",
                    "inboundTag": [cloned_tag],
                    "outboundTag": outbound_tag
                }
                xray_config["routing"]["rules"].insert(0, new_rule)
                
                if is_new:
                    # Prepare Host Payload (Group B OPSEC)
                    new_host_payload = copy.deepcopy(template_host)
                    new_host_payload.pop("id", None)
                    new_host_payload["uuid"] = new_uuid
                    new_host_payload.pop("created_at", None)
                    new_host_payload.pop("updated_at", None)
                    new_host_payload["remark"] = expected_remark
                    new_host_payload["inbound_tag"] = cloned_tag
                    new_host_payload["port"] = this_port
                    host_payloads.append(new_host_payload)
                
                # Update Xray config to accept this specific UUID
                if "settings" in new_inbound and "clients" in new_inbound["settings"] and len(new_inbound["settings"]["clients"]) > 0:
                    client_template = copy.deepcopy(new_inbound["settings"]["clients"][0])
                    client_template["id"] = new_uuid
                    client_template["email"] = matched_host.get("email", new_uuid) if not is_new else new_host_payload.get("email", new_uuid)
                    new_inbound["settings"]["clients"] = [client_template]
                    
            # Delete leftover hosts that were unselected to clean up DB
            for leftover in existing_hosts:
                try:
                    await client.delete(
                        f"{x_pasarguard_host.rstrip('/')}/api/host/{leftover['id']}",
                        headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                        timeout=120.0
                    )
                except Exception:
                    pass
            
            # 4. Save updated core config FIRST so the tags are registered
            core_data["config"] = xray_config
            update_core_resp = await client.put(
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}?restart_nodes=false",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                json=core_data,
                timeout=120.0
            )
            if not update_core_resp.is_success:
                raise HTTPException(status_code=400, detail=f"Failed to update core config: {update_core_resp.text}")
                
            # 5. POST /api/host/ to create new hosts
            for payload in host_payloads:
                create_host_resp = await client.post(
                    f"{x_pasarguard_host.rstrip('/')}/api/host/",
                    headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                    json=payload,
                    timeout=120.0
                )
                if not create_host_resp.is_success:
                    raise HTTPException(status_code=400, detail=f"Failed to create host: {create_host_resp.text}")
            
            # 6. Restart core at the end
            await client.put(
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}?restart_nodes=true",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                json=core_data,
                timeout=120.0
            )
            
            # Save injection state so we know not to re-inject
            port_map = {}
            for conf_tag, conf_port in wireproxy_manager.get_active_proxies().items():
                port_map[str(conf_port)] = conf_tag
            save_injection_state(len(request.locations), port_map)
            
            return {"status": "success", "message": f"Injected {len(request.locations)} locations successfully! Core restarted."}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PasarGuard API Error: {str(e)}")

from fastapi import BackgroundTasks

@app.post("/api/surfshark/restore")
def restore_wireproxy_configs(request: RestoreRequest, background_tasks: BackgroundTasks):
    """Rebuilds wireproxy configs directly from the core Xray config without mutating PasarGuard DB."""
    try:
        # 1. Fetch live Surfshark endpoints for matching
        req = urllib.request.Request('https://api.surfshark.com/v4/server/clusters', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            sf_clusters = json.loads(response.read())
            
        # Create a lookup map: country -> list of clusters
        # Country names in our tags are clean_country(country), we need to reverse match loosely
        clusters_by_country = {}
        for c in sf_clusters:
            if 'pubKey' in c and 'connectionName' in c:
                country = c.get("country", "").replace(" ", "")
                clusters_by_country.setdefault(country, []).append(c)
                
        # 2. Iterate Xray Outbounds
        xray_config = request.core_data.get("config", {})
        outbounds = [ob for ob in xray_config.get("outbounds", []) if ob.get("tag", "").startswith("B-Out-") or ob.get("tag", "").startswith("SurfOut-")]
        
        num_keys = len(request.key_pairs)
        if num_keys == 0:
            raise HTTPException(status_code=400, detail="No key pairs provided.")
            
        locations_per_key = math.ceil(len(outbounds) / num_keys) if outbounds else 1
        
        def _background_restore():
            import time
            import random
            wireproxy_manager._kill_all_wireproxy()
            time.sleep(1.0) # Wait for sockets to close
            restored = 0
            for i, ob in enumerate(outbounds):
                tag = ob.get("tag", "")
                # Extract Country from tag (e.g. B-Out-France-XXXX)
                parts = tag.split("-")
                if len(parts) >= 3:
                    country_part = parts[2]
                else:
                    country_part = "US" # fallback
                    
                # Find a matching cluster
                candidates = clusters_by_country.get(country_part)
                if not candidates:
                    # Try partial match
                    for cname, cls in clusters_by_country.items():
                        if country_part.lower() in cname.lower():
                            candidates = cls
                            break
                
                if not candidates:
                    # If still no match, just pick any valid endpoint to not break the proxy
                    for cls in clusters_by_country.values():
                        candidates = cls
                        break
                        
                if not candidates:
                    continue
                    
                # Pick a random candidate for this country and remove it so we don't reuse it for the same keypair
                loc = random.choice(candidates)
                candidates.remove(loc)
                
                # Find SOCKS port
                try:
                    socks_port = int(ob["settings"]["servers"][0]["port"])
                except (KeyError, IndexError, ValueError):
                    continue
                    
                # Select key pair identically to inject
                kp = request.key_pairs[i // locations_per_key]
                clean_wg_address = kp.wg_address.split('/')[0] + "/32"
                
                
                # Recreate config and start wireproxy!
                wireproxy_manager.start_wireproxy(
                    tag=tag,
                    private_key=kp.private_key,
                    wg_address=clean_wg_address,
                    endpoint=f"{loc['connectionName']}:51820",
                    public_key=loc['pubKey'],
                    local_port=socks_port
                )
                restored += 1
                time.sleep(1.5) # Staggered start to prevent UDP flood / rate limits
            
            print(f"[Restore] All {restored} proxies started. Waiting 60s for handshakes...")
            time.sleep(60)  # Give WireGuard time to handshake
            
            # === POST-RESTORE HEALTH SWEEP ===
            print("[Restore] Running post-restore health sweep...")
            proxies = wireproxy_manager.get_active_proxies()
            dead_tags = []
            alive_count = 0
            
            for tag, port in proxies.items():
                if wireproxy_manager.health_check_proxy(port, timeout=8.0):
                    alive_count += 1
                else:
                    dead_tags.append(tag)
            
            if dead_tags:
                print(f"[Restore] {len(dead_tags)} proxies failed handshake. Swapping endpoints...")
                
                # Refresh clusters for swapping
                try:
                    req2 = urllib.request.Request('https://api.surfshark.com/v4/server/clusters', headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req2, timeout=15) as resp2:
                        fresh_clusters = json.loads(resp2.read())
                    fresh_by_country = {}
                    for c in fresh_clusters:
                        if 'pubKey' in c and 'connectionName' in c:
                            cn = c.get("country", "").replace(" ", "")
                            fresh_by_country.setdefault(cn, []).append(c)
                except Exception as e:
                    print(f"[Restore] Could not fetch clusters for swap: {e}")
                    fresh_by_country = None
                
                if fresh_by_country:
                    swapped = 0
                    for tag in dead_tags:
                        country = _extract_country_from_tag(tag)
                        current_ep = _get_current_endpoint_from_config(tag)
                        alt = _find_alternative_cluster(country, fresh_by_country, current_ep)
                        
                        if alt:
                            new_ep = f"{alt['connectionName']}:51820"
                            wireproxy_manager.swap_proxy_endpoint(tag, new_ep, alt['pubKey'])
                            swapped += 1
                            time.sleep(1.5)
                    
                    print(f"[Restore] Health sweep complete: {alive_count} alive, {swapped}/{len(dead_tags)} swapped.")
                    
                    # Second sweep after 30s for the newly swapped ones
                    if swapped > 0:
                        time.sleep(30)
                        still_dead = 0
                        for tag in dead_tags:
                            port = proxies.get(tag)
                            if port and not wireproxy_manager.health_check_proxy(port, timeout=8.0):
                                still_dead += 1
                        print(f"[Restore] Second check: {still_dead}/{len(dead_tags)} still failing after swap.")
            else:
                print(f"[Restore] All {alive_count} proxies healthy! 🎉")

        background_tasks.add_task(_background_restore)
        return {"status": "success", "message": f"Restoring {len(outbounds)} wireproxy instances in the background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore Error: {str(e)}")

@app.post("/api/pasargard/lifecycle/toggle")
async def toggle_group_b(
    request: LifecycleToggleRequest,
    authorization: str = Header(None),
    x_pasarguard_host: str = Header(None)
):
    if not authorization or not x_pasarguard_host:
        raise HTTPException(status_code=401, detail="Missing credentials.")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{x_pasarguard_host.rstrip('/')}/api/hosts",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                timeout=120.0
            )
            resp.raise_for_status()
            hosts = resp.json()
            
            for host in hosts:
                if host.get("inbound_tag", "").startswith("Surf-") or host.get("inbound_tag", "").startswith("B-In-"):
                    host["enable"] = request.enable
                    host_id = host["id"]
                    
                    await client.put(
                        f"{x_pasarguard_host.rstrip('/')}/api/host/{host_id}",
                        headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                        json=host,
                        timeout=120.0
                    )
            return {"status": "success", "message": f"Group B toggled to {'Enabled' if request.enable else 'Disabled'}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/pasargard/lifecycle/cleanup")
async def cleanup_group_b(
    request: LifecycleCleanupRequest,
    authorization: str = Header(None),
    x_pasarguard_host: str = Header(None)
):
    if not authorization or not x_pasarguard_host:
        raise HTTPException(status_code=401, detail="Missing credentials.")
        
    try:
        async with httpx.AsyncClient() as client:
            # 1. Delete Hosts
            resp = await client.get(
                f"{x_pasarguard_host.rstrip('/')}/api/hosts",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                timeout=120.0
            )
            resp.raise_for_status()
            hosts = resp.json()
            for host in hosts:
                if host.get("inbound_tag", "").startswith("Surf-") or host.get("inbound_tag", "").startswith("B-In-"):
                    await client.delete(
                        f"{x_pasarguard_host.rstrip('/')}/api/host/{host['id']}",
                        headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                        timeout=120.0
                    )
                    
            # 2. Strip Core Config
            core_resp = await client.get(
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                timeout=120.0
            )
            core_resp.raise_for_status()
            core_data = core_resp.json()
            xray_config = core_data["config"]
            tags_to_kill = [o.get("tag") for o in xray_config.get("outbounds", []) if o.get("tag", "").startswith("SurfOut-") or o.get("tag", "").startswith("B-Out-")]
            
            xray_config["inbounds"] = [i for i in xray_config.get("inbounds", []) if not (i.get("tag", "").startswith("Surf-") or i.get("tag", "").startswith("B-In-"))]
            xray_config["outbounds"] = [o for o in xray_config.get("outbounds", []) if not (o.get("tag", "").startswith("SurfOut-") or o.get("tag", "").startswith("B-Out-"))]
            if "routing" in xray_config and "rules" in xray_config["routing"]:
                xray_config["routing"]["rules"] = [
                    r for r in xray_config["routing"]["rules"] 
                    if not (
                        (isinstance(r.get("inboundTag"), list) and any(t.startswith("Surf-") or t.startswith("B-In-") for t in r.get("inboundTag"))) or 
                        (isinstance(r.get("outboundTag"), str) and (r.get("outboundTag", "").startswith("SurfOut-") or r.get("outboundTag", "").startswith("B-Out-")))
                    )
                ]
                
            core_data["config"] = xray_config
            await client.put(
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}?restart_nodes=true",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                json=core_data,
                timeout=120.0
            )
            
            # 4. Stop Wireproxies
            wireproxy_manager.stop_surfshark_proxies(tags_to_kill)
            
            return {"status": "success", "message": "Group B successfully cleaned up."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/pasargard/groups/create")
def create_subscription_group(request: GroupCreateRequest):
    group_file = "subscription_groups.json"
    try:
        groups = []
        if os.path.exists(group_file):
            with open(group_file, "r", encoding="utf-8") as f:
                groups = json.load(f)
        
        new_group = {
            "id": uuid.uuid4().hex[:8],
            "name": request.group_name,
            "core_id": request.core_id,
            "locations": [loc.dict() for loc in request.locations]
        }
        groups.append(new_group)
        
        with open(group_file, "w", encoding="utf-8") as f:
            json.dump(groups, f, indent=2)
            
        return {"status": "success", "message": f"Group '{request.group_name}' created successfully!", "group": new_group}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create group: {str(e)}")

@app.get("/api/logs/wireproxy")
def get_wireproxy_logs():
    log_file = os.path.join(os.path.dirname(__file__), "wireproxy_out.log")
    if not os.path.exists(log_file):
        return {"status": "success", "logs": "No logs available yet."}
    try:
        with open(log_file, "r") as f:
            # Read last 1000 lines max
            lines = f.readlines()
            return {"status": "success", "logs": "".join(lines[-1000:])}
    except Exception as e:
        return {"status": "error", "logs": str(e)}

@app.post("/api/wireproxy/restart")
def restart_all_wireproxies():
    try:
        wireproxy_manager.recover_all_proxies()
        return {"status": "success", "message": "All Wireproxy instances restarted."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/wireproxy/stop")
def stop_all_wireproxies():
    try:
        wireproxy_manager._kill_all_wireproxy()
        return {"status": "success", "message": "All Wireproxy instances stopped."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _fetch_surfshark_clusters():
    """Fetch Surfshark clusters and return a dict of country -> [cluster, cluster, ...]"""
    try:
        req = urllib.request.Request('https://api.surfshark.com/v4/server/clusters', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            sf_clusters = json.loads(response.read())
        
        clusters_by_country = {}
        for c in sf_clusters:
            if 'pubKey' in c and 'connectionName' in c:
                country = c.get("country", "").replace(" ", "")
                clusters_by_country.setdefault(country, []).append(c)
        return clusters_by_country
    except Exception as e:
        print(f"[Watchdog] Failed to fetch Surfshark clusters: {e}")
        return None

def _extract_country_from_tag(tag: str) -> str:
    """Extract country name from a tag like 'B-Out-France-XXXX' or 'SurfOut-France-XXXX'."""
    parts = tag.split("-")
    if len(parts) >= 3:
        return parts[2]
    return ""

def _find_alternative_cluster(country_part: str, clusters_by_country: dict, current_endpoint: str = None):
    """Find an alternative Surfshark cluster for the given country, avoiding current_endpoint."""
    # Exact match
    candidates = clusters_by_country.get(country_part, [])
    if not candidates:
        # Partial match
        for cname, cls in clusters_by_country.items():
            if country_part.lower() in cname.lower():
                candidates = cls
                break
    
    if not candidates:
        return None
    
    import random
    # Filter out the current endpoint if possible
    if current_endpoint and len(candidates) > 1:
        filtered = [c for c in candidates if c['connectionName'] not in current_endpoint]
        if filtered:
            return random.choice(filtered)
    
    return random.choice(candidates)

def _get_current_endpoint_from_config(tag: str) -> str:
    """Read the current Endpoint from a wireproxy config file."""
    import re
    conf_path = os.path.join(os.path.dirname(__file__), "wireproxy_configs", f"{tag}.conf")
    try:
        with open(conf_path, 'r') as f:
            for line in f:
                if 'Endpoint' in line and '=' in line:
                    return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return ""

def _watchdog_loop():
    """Background loop: check all active proxies and self-heal dead ones by swapping endpoints."""
    import time
    INTERVAL = 120  # 2 minutes
    MAX_CONSECUTIVE_FAILS = 2  # Swap endpoint after 2 consecutive failed checks
    
    fail_counts = {}  # tag -> consecutive fail count
    
    # Wait 90 seconds on first boot to let everything stabilize
    time.sleep(90)
    
    # Cache clusters, refresh every 10 minutes
    clusters_cache = None
    last_cluster_fetch = 0
    CLUSTER_REFRESH_INTERVAL = 600  # 10 minutes
    
    while True:
        try:
            proxies = wireproxy_manager.get_active_proxies()
            
            if not proxies:
                time.sleep(INTERVAL)
                continue
            
            # Refresh Surfshark clusters if needed
            now = time.time()
            if clusters_cache is None or (now - last_cluster_fetch) > CLUSTER_REFRESH_INTERVAL:
                clusters_cache = _fetch_surfshark_clusters()
                last_cluster_fetch = now
            
            dead_count = 0
            alive_count = 0
            swapped_count = 0
            
            for tag, port in proxies.items():
                is_alive = wireproxy_manager.health_check_proxy(port)
                
                if is_alive:
                    alive_count += 1
                    fail_counts[tag] = 0  # Reset on success
                else:
                    fail_counts[tag] = fail_counts.get(tag, 0) + 1
                    
                    if fail_counts[tag] >= MAX_CONSECUTIVE_FAILS:
                        country = _extract_country_from_tag(tag)
                        current_ep = _get_current_endpoint_from_config(tag)
                        
                        if clusters_cache and country:
                            # Try to find an ALTERNATIVE server for the same country
                            alt = _find_alternative_cluster(country, clusters_cache, current_ep)
                            if alt:
                                new_ep = f"{alt['connectionName']}:51820"
                                new_pk = alt['pubKey']
                                print(f"[Watchdog] {tag} dead for {fail_counts[tag]} checks. SWAPPING to {alt['connectionName']}")
                                wireproxy_manager.swap_proxy_endpoint(tag, new_ep, new_pk)
                                swapped_count += 1
                            else:
                                # No alternative found, just restart
                                print(f"[Watchdog] {tag} dead, no alternative server found. Restarting as-is.")
                                wireproxy_manager.restart_single_proxy(tag)
                                dead_count += 1
                        else:
                            # No cluster data available, just restart
                            print(f"[Watchdog] {tag} dead, no cluster data. Restarting as-is.")
                            wireproxy_manager.restart_single_proxy(tag)
                            dead_count += 1
                        
                        fail_counts[tag] = 0  # Reset after action
                        time.sleep(1.5)  # Gap between swaps to avoid rate limiting
            
            total = alive_count + dead_count + swapped_count
            if dead_count > 0 or swapped_count > 0:
                print(f"[Watchdog] Cycle: {alive_count}/{total} alive, {swapped_count} swapped, {dead_count} restarted.")
                
        except Exception as e:
            print(f"[Watchdog] Error during health check cycle: {e}")
        
        time.sleep(INTERVAL)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8088)

