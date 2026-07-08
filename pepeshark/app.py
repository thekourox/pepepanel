import os
import json
import uuid
import urllib.request
import httpx
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import wireproxy_manager

app = FastAPI(title="Pasargard VPN Automator")

os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

class Location(BaseModel):
    country: str
    countryCode: str
    location: str
    endpoint: str
    publicKey: str

class InjectRequest(BaseModel):
    private_key: str
    wg_address: str
    core_id: str
    template_inbound_id: str
    locations: List[Location]

class GroupCreateRequest(BaseModel):
    group_name: str
    core_id: str
    locations: List[Location]

class LifecycleToggleRequest(BaseModel):
    core_id: str
    enable: bool

class LifecycleCleanupRequest(BaseModel):
    core_id: str

@app.get("/", response_class=HTMLResponse)
async def serve_gui(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

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
                timeout=10.0
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
                timeout=10.0
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
                timeout=10.0
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
            
            # 2. Fetch Template Host
            host_resp = await client.get(
                f"{x_pasarguard_host.rstrip('/')}/api/host/{request.template_inbound_id}",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                timeout=10.0
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
            
            # Find max SOCKS port for wireproxy local SOCKS5 servers
            used_socks_ports = [
                server.get("port") 
                for ob in xray_config["outbounds"] 
                if ob.get("protocol") == "socks" 
                for server in ob.get("settings", {}).get("servers", [])
            ]
            used_socks_ports = [p for p in used_socks_ports if isinstance(p, int)]
            local_port = max(used_socks_ports) + 1 if used_socks_ports and max(used_socks_ports) >= 20000 else 20000
            
            host_payloads = []
            
            # 3. Update Core Config (Inbounds, Outbounds, Routing)
            clean_wg_address = request.wg_address.split('/')[0] + "/32"
            
            for loc in request.locations:
                safe_country = loc.country.replace(" ", "")
                import random, string
                suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
                outbound_tag = f"B-Out-{safe_country}-{suffix}"
                cloned_tag = f"B-In-{safe_country}-{suffix}"
                
                # Start wireproxy SOCKS5 process locally
                wireproxy_manager.start_wireproxy(
                    tag=outbound_tag,
                    private_key=request.private_key,
                    wg_address=clean_wg_address,
                    endpoint=f"{loc.endpoint}:51820",
                    public_key=loc.publicKey,
                    local_port=local_port
                )
                
                # Clone Inbound
                import copy
                new_inbound = copy.deepcopy(template_inbound)
                new_inbound["tag"] = cloned_tag
                new_inbound["port"] = next_port
                xray_config["inbounds"].append(new_inbound)
                
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
                next_port += 1
                
                xray_config["routing"]["rules"] = [r for r in xray_config["routing"]["rules"] if r.get("outboundTag") != outbound_tag]
                new_rule = {
                    "type": "field",
                    "inboundTag": [cloned_tag],
                    "outboundTag": outbound_tag
                }
                xray_config["routing"]["rules"].insert(0, new_rule)
                
                # Prepare Host Payload (Group B OPSEC)
                def get_flag(cc: str) -> str:
                    if not cc or len(cc) != 2: return ""
                    return chr(ord(cc[0].upper()) + 127397) + chr(ord(cc[1].upper()) + 127397)
                
                flag = get_flag(loc.countryCode)
                location_part = f" - {loc.location}" if loc.location and loc.location.strip() != loc.country.strip() else ""
                new_name_remark = f"{loc.country}{location_part} {flag}"
                new_host_payload = dict(template_host)
                new_host_payload.pop("id", None)
                new_host_payload["name"] = new_name_remark
                new_host_payload["remark"] = new_name_remark
                new_host_payload["inbound_tag"] = cloned_tag
                new_host_payload["port"] = new_inbound["port"]
                host_payloads.append(new_host_payload)
            
            # 4. Save updated core config FIRST so the tags are registered
            core_data["config"] = xray_config
            update_core_resp = await client.put(
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}?restart_nodes=false",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                json=core_data,
                timeout=10.0
            )
            if not update_core_resp.is_success:
                raise HTTPException(status_code=400, detail=f"Failed to update core config: {update_core_resp.text}")
                
            # 5. POST /api/host/ to create new hosts
            for payload in host_payloads:
                create_host_resp = await client.post(
                    f"{x_pasarguard_host.rstrip('/')}/api/host/",
                    headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                    json=payload,
                    timeout=10.0
                )
                if not create_host_resp.is_success:
                    raise HTTPException(status_code=400, detail=f"Failed to create host: {create_host_resp.text}")
            
            # 6. Restart core at the end
            await client.post(
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}/restart",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"}
            )
            
            return {"status": "success", "message": "Injected successfully via Wireproxy and restarted core!"}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PasarGuard API Error: {str(e)}")

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
                timeout=10.0
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
                        timeout=10.0
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
                timeout=10.0
            )
            resp.raise_for_status()
            hosts = resp.json()
            for host in hosts:
                if host.get("inbound_tag", "").startswith("Surf-") or host.get("inbound_tag", "").startswith("B-In-"):
                    await client.delete(
                        f"{x_pasarguard_host.rstrip('/')}/api/host/{host['id']}",
                        headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                        timeout=10.0
                    )
                    
            # 2. Strip Core Config
            core_resp = await client.get(
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                timeout=10.0
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
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}?restart_nodes=false",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"},
                json=core_data,
                timeout=10.0
            )
            
            # 3. Restart Core
            await client.post(
                f"{x_pasarguard_host.rstrip('/')}/api/core/{request.core_id}/restart",
                headers={"Authorization": f"Bearer {authorization.replace('Bearer ', '')}"}
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8088)
