document.addEventListener('DOMContentLoaded', () => {
    // ============ DOM Elements ============
    const locationsList = document.getElementById('locationsList');
    const coreSelector = document.getElementById('coreSelector');
    const inboundSelector = document.getElementById('inboundSelector');
    const injectBtn = document.getElementById('injectBtn');
    const quickInjectBtn = document.getElementById('quickInjectBtn');
    const pasarguardUrlInput = document.getElementById('pasarguardUrl');
    const pasarguardTokenInput = document.getElementById('pasarguardToken');
    const statusToast = document.getElementById('statusToast');
    const logConsole = document.getElementById('logConsole');
    const logConsoleFull = document.getElementById('logConsoleFull');

    // Monitor elements
    const monitorActive = document.getElementById('monitorActive');
    const monitorDead = document.getElementById('monitorDead');
    const monitorKeys = document.getElementById('monitorKeys');
    const locationCounter = document.getElementById('locationCounter');
    
    // Injection state
    let isAlreadyInjected = false;

    // ============ Sidebar Navigation ============
    const navItems = document.querySelectorAll('.nav-item');
    const sections = document.querySelectorAll('.content-section');

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const target = item.dataset.section;

            navItems.forEach(n => n.classList.remove('active'));
            item.classList.add('active');

            sections.forEach(s => s.classList.remove('active'));
            document.getElementById(`sec-${target}`).classList.add('active');

            // Close mobile sidebar
            document.getElementById('sidebar').classList.remove('open');
        });
    });

    // Mobile menu toggle
    const menuToggle = document.getElementById('menuToggle');
    if (menuToggle) {
        menuToggle.addEventListener('click', () => {
            document.getElementById('sidebar').classList.toggle('open');
        });
    }

    // ============ LocalStorage Persistence ============
    if (localStorage.getItem('pasarguardUrl')) {
        pasarguardUrlInput.value = localStorage.getItem('pasarguardUrl');
    }
    if (localStorage.getItem('pasarguardToken')) {
        pasarguardTokenInput.value = localStorage.getItem('pasarguardToken');
    }

    pasarguardUrlInput.addEventListener('change', () => {
        localStorage.setItem('pasarguardUrl', pasarguardUrlInput.value);
        initializeApp();
    });
    pasarguardTokenInput.addEventListener('change', () => {
        localStorage.setItem('pasarguardToken', pasarguardTokenInput.value);
        initializeApp();
    });

    // ============ Initialize App ============
    async function initializeApp() {
        try {
            const locResponse = await fetch('/api/surfshark/locations');
            const locations = await locResponse.json();
            renderLocations(locations);

            // Fetch injection status
            await fetchInjectionStatus();

            if (!pasarguardTokenInput.value || !pasarguardUrlInput.value) {
                coreSelector.innerHTML = '<option value="">Enter Settings first...</option>';
                return;
            }
            const coresResponse = await fetch('/api/pasargard/cores', {
                headers: {
                    'Authorization': `Bearer ${pasarguardTokenInput.value}`,
                    'X-Pasarguard-Host': pasarguardUrlInput.value
                }
            });
            if (!coresResponse.ok) throw new Error("Invalid API Token or Host");
            const cores = await coresResponse.json();
            renderCores(cores);
        } catch (error) {
            showStatus('Failed to connect to backend: ' + error.message, 'error');
        }
    }

    // ============ Render Locations ============
    function renderLocations(locations) {
        locationsList.innerHTML = '';
        locations.forEach(loc => {
            const div = document.createElement('div');
            div.className = 'location-item';
            div.innerHTML = `
                <input type="checkbox" id="loc_${loc.endpoint}" value='${JSON.stringify(loc)}'>
                <label class="location-info" for="loc_${loc.endpoint}">
                    <span class="loc-country">${loc.country}</span>
                    <span class="loc-city">${loc.location}</span>
                </label>
            `;
            locationsList.appendChild(div);

            const cb = div.querySelector('input');
            cb.addEventListener('change', () => {
                div.classList.toggle('selected', cb.checked);
                updateLocationCounter();
                validateForm();
            });
        });
    }

    function updateLocationCounter() {
        const count = document.querySelectorAll('#locationsList input:checked').length;
        if (locationCounter) locationCounter.textContent = `${count} selected`;
    }

    // ============ Render Cores ============
    function renderCores(cores) {
        coreSelector.innerHTML = '<option value="">Select a Core</option>';
        if (cores.length === 0) {
            coreSelector.innerHTML = '<option value="">No Cores Detected</option>';
            return;
        }
        cores.forEach(core => {
            const opt = document.createElement('option');
            opt.value = core.id;
            opt.textContent = `Core ${core.id} (${core.setting_key})`;
            coreSelector.appendChild(opt);
        });
        coreSelector.disabled = false;
    }

    // Core selection -> fetch inbounds
    coreSelector.addEventListener('change', async (e) => {
        const coreId = e.target.value;
        inboundSelector.innerHTML = '<option value="">Loading...</option>';
        inboundSelector.disabled = true;

        if (!coreId) {
            inboundSelector.innerHTML = '<option value="">Select a Core First...</option>';
            validateForm();
            return;
        }

        try {
            const response = await fetch(`/api/pasargard/inbounds?core_id=${coreId}`, {
                headers: {
                    'Authorization': `Bearer ${pasarguardTokenInput.value}`,
                    'X-Pasarguard-Host': pasarguardUrlInput.value
                }
            });
            const inbounds = await response.json();

            inboundSelector.innerHTML = '<option value="">Select Template</option>';
            if (inbounds.length === 0) {
                inboundSelector.innerHTML = '<option value="">No Inbounds Found</option>';
            } else {
                inbounds.forEach(inb => {
                    const opt = document.createElement('option');
                    opt.value = inb.id;
                    opt.textContent = `[${inb.id}] ${inb.remark} (Port: ${inb.port})`;
                    inboundSelector.appendChild(opt);
                });
                inboundSelector.disabled = false;
            }
        } catch (error) {
            inboundSelector.innerHTML = '<option value="">Error Loading</option>';
        }
        validateForm();
    });

    // ============ Key Pairs ============
    const addKeyPairBtn = document.getElementById('addKeyPairBtn');
    const keyPairsContainer = document.getElementById('keyPairsContainer');
    let keyPairCount = 0;

    function createKeyPairElement(pkValue = "", wgValue = "10.14.0.2/16", isFirst = false) {
        keyPairCount++;
        const div = document.createElement('div');
        div.className = 'key-pair-card';
        div.innerHTML = `
            <div class="kp-header">
                <span class="kp-label">Key Pair ${keyPairCount}</span>
                ${!isFirst ? '<button class="kp-remove" type="button">Remove</button>' : ''}
            </div>
            <input type="password" class="pk-input" placeholder="WireGuard Private Key" value="${pkValue}">
            <input type="text" class="wg-input" placeholder="WG Address (e.g. 10.14.0.2/16)" value="${wgValue}">
        `;

        const removeBtn = div.querySelector('.kp-remove');
        if (removeBtn) {
            removeBtn.addEventListener('click', () => {
                div.remove();
                updateMonitorKeys();
                validateForm();
            });
        }

        div.querySelectorAll('input').forEach(inp => inp.addEventListener('input', () => {
            updateMonitorKeys();
            validateForm();
        }));
        keyPairsContainer.appendChild(div);
        updateMonitorKeys();
    }

    function updateMonitorKeys() {
        const count = getValidKeyPairs().length;
        if (monitorKeys) monitorKeys.textContent = count;
    }

    // Load saved keys or create default
    const savedKeys = JSON.parse(localStorage.getItem('surfsharkKeyPairs') || '[]');
    if (savedKeys && savedKeys.length > 0) {
        savedKeys.forEach((kp, index) => {
            createKeyPairElement(kp.private_key, kp.wg_address, index === 0);
        });
    } else {
        createKeyPairElement('', '10.14.0.2/16', true);
    }

    addKeyPairBtn.addEventListener('click', () => {
        createKeyPairElement();
        validateForm();
    });

    function getValidKeyPairs() {
        const pairs = [];
        document.querySelectorAll('.key-pair-card').forEach(group => {
            const pk = group.querySelector('.pk-input').value.trim();
            const wg = group.querySelector('.wg-input').value.trim();
            if (pk && wg) pairs.push({ private_key: pk, wg_address: wg });
        });
        return pairs;
    }

    // ============ Search & Select ============
    const searchInput = document.getElementById('locationSearch');
    const selectAllCheckbox = document.getElementById('selectAllLocations');
    const selectNonTorBtn = document.getElementById('selectNonTor');
    const createGroupBtn = document.getElementById('createGroupBtn');
    const quickCreateGroupBtn = document.getElementById('quickCreateGroupBtn');
    const groupNameInput = document.getElementById('groupName');

    const torCountries = [
        "United States", "Netherlands", "Germany", "Sweden", "Austria", "Luxembourg",
        "Romania", "France", "Norway", "Singapore", "Switzerland", "Iceland", "Croatia",
        "Italy", "Hungary", "Denmark", "Bulgaria", "Ukraine", "Czechia", "Finland",
        "United Kingdom", "Canada", "Poland", "Indonesia", "South Africa", "Moldova",
        "Taiwan", "Malaysia", "Hong Kong", "Vietnam", "Türkiye", "Spain", "Japan",
        "Chile", "Peru", "United Arab Emirates", "New Zealand", "Cyprus", "Argentina",
        "Costa Rica", "Estonia", "India", "Portugal", "Mexico", "Greece", "Azerbaijan",
        "Israel", "South Korea", "Belgium", "Seychelles", "Lithuania", "Australia", "Tunisia"
    ];

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        document.querySelectorAll('.location-item').forEach(item => {
            item.style.display = item.textContent.toLowerCase().includes(query) ? '' : 'none';
        });
    });

    selectAllCheckbox.addEventListener('change', (e) => {
        const isChecked = e.target.checked;
        Array.from(document.querySelectorAll('.location-item'))
            .filter(item => item.style.display !== 'none')
            .forEach(item => {
                const cb = item.querySelector('input[type="checkbox"]');
                cb.checked = isChecked;
                item.classList.toggle('selected', isChecked);
            });
        updateLocationCounter();
        validateForm();
    });

    if (selectNonTorBtn) {
        selectNonTorBtn.addEventListener('click', () => {
            Array.from(document.querySelectorAll('.location-item'))
                .filter(item => item.style.display !== 'none')
                .forEach(item => {
                    const cb = item.querySelector('input[type="checkbox"]');
                    const locData = JSON.parse(cb.value);
                    const shouldCheck = !torCountries.includes(locData.country);
                    cb.checked = shouldCheck;
                    item.classList.toggle('selected', shouldCheck);
                });
            updateLocationCounter();
            validateForm();
        });
    }

    inboundSelector.addEventListener('change', validateForm);
    if (groupNameInput) groupNameInput.addEventListener('input', validateForm);

    // ============ Validation ============
    function validateForm() {
        const selectedLocations = document.querySelectorAll('#locationsList input:checked');
        const hasCore = coreSelector.value !== "";
        const hasInbound = inboundSelector.value !== "";
        const validKeyPairs = getValidKeyPairs();

        if (validKeyPairs.length > 0) {
            localStorage.setItem('surfsharkKeyPairs', JSON.stringify(validKeyPairs));
        }

        const canInject = selectedLocations.length > 0 && hasCore && hasInbound && validKeyPairs.length > 0;
        injectBtn.disabled = !canInject;
        quickInjectBtn.disabled = !canInject;

        const canGroup = selectedLocations.length > 0 && hasCore;
        createGroupBtn.disabled = !canGroup;
        quickCreateGroupBtn.disabled = !canGroup;

        // Update monitor
        if (monitorActive) monitorActive.textContent = selectedLocations.length;

        // Lifecycle buttons
        const toggleBtn = document.getElementById('toggleGroupBBtn');
        const enableBtn = document.getElementById('enableGroupBBtn');
        const cleanupBtn = document.getElementById('cleanupGroupBBtn');
        if (toggleBtn) toggleBtn.disabled = !hasCore;
        if (enableBtn) enableBtn.disabled = !hasCore;
        if (cleanupBtn) cleanupBtn.disabled = !hasCore;
    }

    // ============ Inject (with re-inject guard) ============
    function showReInjectWarning(btn) {
        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay';
            overlay.innerHTML = `
                <div class="modal-box">
                    <h3>⚠️ Re-Injection Warning</h3>
                    <p>An injection is already active with <strong>${isAlreadyInjected}</strong> locations configured.
                    Re-injecting will <strong>recreate all inbounds/hosts</strong> in PasarGuard, which may remove them from subscription groups.
                    <br><br>Are you sure you want to proceed?</p>
                    <div class="modal-actions">
                        <button class="modal-cancel">Cancel</button>
                        <button class="modal-confirm">Yes, Re-Inject</button>
                    </div>
                </div>
            `;
            document.body.appendChild(overlay);
            overlay.querySelector('.modal-cancel').addEventListener('click', () => {
                overlay.remove();
                resolve(false);
            });
            overlay.querySelector('.modal-confirm').addEventListener('click', () => {
                overlay.remove();
                resolve(true);
            });
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) { overlay.remove(); resolve(false); }
            });
        });
    }

    async function doInject(btn) {
        // Guard: if already injected, show warning
        if (isAlreadyInjected) {
            const confirmed = await showReInjectWarning(btn);
            if (!confirmed) return;
        }

        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Injecting...';

        const selectedLocations = Array.from(document.querySelectorAll('#locationsList input:checked'))
            .map(cb => JSON.parse(cb.value));

        const payload = {
            key_pairs: getValidKeyPairs(),
            core_id: coreSelector.value,
            template_inbound_id: inboundSelector.value,
            locations: selectedLocations,
            server_ip: document.getElementById('isLocal').checked ? '127.0.0.1' : window.location.hostname
        };

        try {
            const response = await fetch('/api/pasargard/inject', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${pasarguardTokenInput.value}`,
                    'X-Pasarguard-Host': pasarguardUrlInput.value
                },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (response.ok) {
                showStatus(result.message || 'Injected successfully!', 'success');
                await fetchInjectionStatus(); // refresh status
            } else {
                showStatus(`Error: ${result.detail || 'Injection failed.'}`, 'error');
            }
        } catch (error) {
            showStatus('Network error while injecting.', 'error');
        } finally {
            btn.textContent = origText;
            btn.disabled = false;
            validateForm();
        }
    }

    injectBtn.addEventListener('click', () => doInject(injectBtn));
    quickInjectBtn.addEventListener('click', () => doInject(quickInjectBtn));

    // ============ Create Group ============
    async function doCreateGroup(btn) {
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Creating...';

        const selectedLocations = Array.from(document.querySelectorAll('#locationsList input:checked'))
            .map(cb => JSON.parse(cb.value));

        const payload = {
            group_name: groupNameInput.value.trim() || 'Unnamed Group',
            core_id: coreSelector.value,
            locations: selectedLocations
        };

        try {
            const response = await fetch('/api/pasargard/groups/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (response.ok) {
                showStatus(result.message || 'Group created!', 'success');
                groupNameInput.value = '';
                validateForm();
            } else {
                showStatus(`Error: ${result.detail || 'Failed.'}`, 'error');
            }
        } catch (error) {
            showStatus('Network error.', 'error');
        } finally {
            btn.textContent = origText;
            btn.disabled = false;
        }
    }

    createGroupBtn.addEventListener('click', () => doCreateGroup(createGroupBtn));
    quickCreateGroupBtn.addEventListener('click', () => doCreateGroup(quickCreateGroupBtn));

    // ============ Lifecycle ============
    const toggleBtn = document.getElementById('toggleGroupBBtn');
    const enableBtn = document.getElementById('enableGroupBBtn');
    const cleanupBtn = document.getElementById('cleanupGroupBBtn');

    async function handleLifecycle(endpoint, payload, button) {
        const origText = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<span>Working...</span>';

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${pasarguardTokenInput.value}`,
                    'X-Pasarguard-Host': pasarguardUrlInput.value
                },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            showStatus(response.ok ? result.message : `Error: ${result.detail}`, response.ok ? 'success' : 'error');
        } catch (error) {
            showStatus('Network error.', 'error');
        } finally {
            button.innerHTML = origText;
            validateForm();
        }
    }

    if (toggleBtn) toggleBtn.addEventListener('click', () => {
        if (confirm('Disable all Group B proxies?'))
            handleLifecycle('/api/pasargard/lifecycle/toggle', { core_id: coreSelector.value, enable: false }, toggleBtn);
    });
    if (enableBtn) enableBtn.addEventListener('click', () => {
        handleLifecycle('/api/pasargard/lifecycle/toggle', { core_id: coreSelector.value, enable: true }, enableBtn);
    });
    if (cleanupBtn) cleanupBtn.addEventListener('click', () => {
        if (confirm('WARNING: This will permanently delete ALL Group B configs. Proceed?'))
            handleLifecycle('/api/pasargard/lifecycle/cleanup', { core_id: coreSelector.value }, cleanupBtn);
    });

    // ============ Service Controls ============
    const restartServiceBtn = document.getElementById('restartServiceBtn');
    const stopServiceBtn = document.getElementById('stopServiceBtn');

    if (restartServiceBtn) {
        restartServiceBtn.addEventListener('click', async () => {
            restartServiceBtn.disabled = true;
            try {
                const response = await fetch('/api/wireproxy/restart', { method: 'POST' });
                const data = await response.json();
                showStatus(data.message || 'Restart signal sent.', data.status === 'success' ? 'success' : 'error');
            } catch (e) {
                showStatus('Network error', 'error');
            } finally {
                restartServiceBtn.disabled = false;
            }
        });
    }

    if (stopServiceBtn) {
        stopServiceBtn.addEventListener('click', async () => {
            stopServiceBtn.disabled = true;
            try {
                const response = await fetch('/api/wireproxy/stop', { method: 'POST' });
                const data = await response.json();
                showStatus(data.message || 'Stop signal sent.', data.status === 'success' ? 'success' : 'error');
            } catch (e) {
                showStatus('Network error', 'error');
            } finally {
                stopServiceBtn.disabled = false;
            }
        });
    }

    // ============ Logs ============
    const fetchLogsBtn = document.getElementById('fetchLogsBtn');
    const fetchLogsBtnFull = document.getElementById('fetchLogsBtnFull');

    async function fetchLogs() {
        try {
            const response = await fetch('/api/logs/wireproxy');
            const data = await response.json();
            if (response.ok && data.status === 'success') {
                const logText = data.logs || "No logs yet.";
                if (logConsole) {
                    const wasAtBottom = logConsole.scrollHeight - logConsole.clientHeight <= logConsole.scrollTop + 10;
                    logConsole.textContent = logText;
                    if (wasAtBottom) logConsole.scrollTop = logConsole.scrollHeight;
                }
                if (logConsoleFull) {
                    const wasAtBottom2 = logConsoleFull.scrollHeight - logConsoleFull.clientHeight <= logConsoleFull.scrollTop + 10;
                    logConsoleFull.textContent = logText;
                    if (wasAtBottom2) logConsoleFull.scrollTop = logConsoleFull.scrollHeight;
                }
            }
        } catch (e) {
            // silent
        }
    }

    if (fetchLogsBtn) fetchLogsBtn.addEventListener('click', fetchLogs);
    if (fetchLogsBtnFull) fetchLogsBtnFull.addEventListener('click', fetchLogs);

    // ============ Injection Status Polling ============
    async function fetchInjectionStatus() {
        try {
            const resp = await fetch('/api/injection/status');
            const data = await resp.json();
            
            const banner = document.getElementById('injectionBanner');
            const bannerDetail = document.getElementById('bannerDetail');
            const bannerAlive = document.getElementById('bannerAlive');
            const bannerDead = document.getElementById('bannerDead');
            
            if (data.injected) {
                isAlreadyInjected = data.location_count;
                if (banner) {
                    banner.style.display = 'flex';
                    bannerDetail.textContent = `${data.location_count} locations configured • ${data.total_configs} configs on disk`;
                    bannerAlive.textContent = `${data.alive} alive`;
                    bannerDead.textContent = `${data.dead} dead`;
                }
                if (monitorActive) monitorActive.textContent = data.alive;
                if (monitorDead) monitorDead.textContent = data.dead;
            } else {
                isAlreadyInjected = false;
                if (banner) banner.style.display = 'none';
                if (monitorActive) monitorActive.textContent = '0';
                if (monitorDead) monitorDead.textContent = '0';
            }
        } catch (e) {
            // silent
        }
    }

    // ============ Status Toast ============
    let toastTimer = null;
    function showStatus(msg, type) {
        if (!msg) return;
        statusToast.textContent = msg;
        statusToast.className = `status-toast visible ${type}`;
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(() => {
            statusToast.className = 'status-toast';
        }, 5000);
    }

    // ============ Boot ============
    fetchLogs();
    setInterval(fetchLogs, 3000);
    setInterval(fetchInjectionStatus, 15000); // Poll health every 15s
    initializeApp();
    updateMonitorKeys();
});
