document.addEventListener('DOMContentLoaded', () => {
    const locationsList = document.getElementById('locationsList');
    const coreSelector = document.getElementById('coreSelector');
    const inboundSelector = document.getElementById('inboundSelector');
    const injectBtn = document.getElementById('injectBtn');
    const privateKeyInput = document.getElementById('privateKey');
    const wgAddressInput = document.getElementById('wgAddress');
    const statusMessage = document.getElementById('statusMessage');
    const pasarguardUrlInput = document.getElementById('pasarguardUrl');
    const pasarguardTokenInput = document.getElementById('pasarguardToken');

    // Load from local storage
    if(localStorage.getItem('pasarguardUrl')) {
        pasarguardUrlInput.value = localStorage.getItem('pasarguardUrl');
    }
    if(localStorage.getItem('pasarguardToken')) {
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

    // Fetch Initial Data (Cores & Surfshark Locations)
    async function initializeApp() {
        try {
            // 1. Fetch Surfshark Locations
            const locResponse = await fetch('/api/surfshark/locations');
            const locations = await locResponse.json();
            renderLocations(locations);

            // 2. Fetch Active Cores
            if (!pasarguardTokenInput.value || !pasarguardUrlInput.value) {
                coreSelector.innerHTML = '<option value="">Enter PasarGuard Settings first...</option>';
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
            showStatus('Failed to connect to backend.', 'error');
        }
    }

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
            
            // Add listener to enable/disable button
            div.querySelector('input').addEventListener('change', validateForm);
        });
    }

    function renderCores(cores) {
        coreSelector.innerHTML = '<option value="">Select a Core</option>';
        if (cores.length === 0) {
            coreSelector.innerHTML = '<option value="">No Cores Detected</option>';
            return;
        }
        
        cores.forEach(core => {
            const opt = document.createElement('option');
            opt.value = core.id;
            opt.textContent = `Core ID: ${core.id} (${core.setting_key})`;
            coreSelector.appendChild(opt);
        });
        coreSelector.disabled = false;
    }

    // When a Core is selected, fetch its Inbounds
    coreSelector.addEventListener('change', async (e) => {
        const coreId = e.target.value;
        inboundSelector.innerHTML = '<option value="">Loading Inbounds...</option>';
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
            
            inboundSelector.innerHTML = '<option value="">Select Template Inbound</option>';
            if (inbounds.length === 0) {
                inboundSelector.innerHTML = '<option value="">No Inbounds Found</option>';
            } else {
                inbounds.forEach(inb => {
                    const opt = document.createElement('option');
                    opt.value = inb.id;
                    opt.textContent = `[ID: ${inb.id}] ${inb.remark} (Port: ${inb.port})`;
                    inboundSelector.appendChild(opt);
                });
                inboundSelector.disabled = false;
            }
        } catch (error) {
            inboundSelector.innerHTML = '<option value="">Error Loading Inbounds</option>';
        }
        validateForm();
    });

    const createGroupBtn = document.getElementById('createGroupBtn');
    const groupNameInput = document.getElementById('groupName');
    const searchInput = document.getElementById('locationSearch');
    const selectAllCheckbox = document.getElementById('selectAllLocations');
    const selectNonTorBtn = document.getElementById('selectNonTor');

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

    // Handle Search functionality
    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        const items = document.querySelectorAll('.location-item');
        items.forEach(item => {
            const text = item.textContent.toLowerCase();
            item.style.display = text.includes(query) ? '' : 'none';
        });
    });

    // Handle Select All functionality
    selectAllCheckbox.addEventListener('change', (e) => {
        const isChecked = e.target.checked;
        const visibleCheckboxes = Array.from(document.querySelectorAll('.location-item'))
            .filter(item => item.style.display !== 'none')
            .map(item => item.querySelector('input[type="checkbox"]'));
        
        visibleCheckboxes.forEach(cb => {
            cb.checked = isChecked;
        });
        validateForm();
    });

    // Handle Select Non-Tor functionality
    if (selectNonTorBtn) {
        selectNonTorBtn.addEventListener('click', () => {
            const visibleItems = Array.from(document.querySelectorAll('.location-item'))
                .filter(item => item.style.display !== 'none');
            
            visibleItems.forEach(item => {
                const cb = item.querySelector('input[type="checkbox"]');
                const locData = JSON.parse(cb.value);
                // Check if the country is NOT in the Tor list
                if (!torCountries.includes(locData.country)) {
                    cb.checked = true;
                } else {
                    cb.checked = false;
                }
            });
            validateForm();
        });
    }

    inboundSelector.addEventListener('change', validateForm);
    privateKeyInput.addEventListener('input', validateForm);
    wgAddressInput.addEventListener('input', validateForm);
    groupNameInput.addEventListener('input', validateForm);

    function validateForm() {
        const hasKey = privateKeyInput.value.trim().length > 10;
        const hasWgAddr = wgAddressInput.value.trim().length > 5;
        const hasCore = coreSelector.value !== '';
        const hasInbound = inboundSelector.value !== '';
        const hasLocation = document.querySelectorAll('#locationsList input:checked').length > 0;
        const hasGroupName = groupNameInput.value.trim().length > 0;

        injectBtn.disabled = !(hasKey && hasWgAddr && hasCore && hasInbound && hasLocation);
        createGroupBtn.disabled = !(hasCore && hasLocation && hasGroupName);
        
        const toggleBtn = document.getElementById('toggleGroupBBtn');
        const enableBtn = document.getElementById('enableGroupBBtn');
        const cleanupBtn = document.getElementById('cleanupGroupBBtn');
        
        if (toggleBtn && enableBtn && cleanupBtn) {
            toggleBtn.disabled = !hasCore;
            enableBtn.disabled = !hasCore;
            cleanupBtn.disabled = !hasCore;
        }
    }

    // Handle Injection
    injectBtn.addEventListener('click', async () => {
        injectBtn.disabled = true;
        injectBtn.textContent = 'Injecting...';
        showStatus('');

        const selectedLocations = Array.from(document.querySelectorAll('#locationsList input:checked'))
            .map(cb => JSON.parse(cb.value));

        const payload = {
            private_key: privateKeyInput.value.trim(),
            wg_address: wgAddressInput.value.trim(),
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
                showStatus(result.message || 'Successfully injected and restarted Pasargard!', 'success');
            } else {
                showStatus(`Error: ${result.detail || 'Injection failed.'}`, 'error');
                injectBtn.disabled = false;
            }
        } catch (error) {
            showStatus('Network error while injecting.', 'error');
            injectBtn.disabled = false;
        } finally {
            injectBtn.textContent = 'Inject Native WireGuard';
        }
    });

    // Handle Create Group
    createGroupBtn.addEventListener('click', async () => {
        createGroupBtn.disabled = true;
        createGroupBtn.textContent = 'Creating...';
        showStatus('');

        const selectedLocations = Array.from(document.querySelectorAll('#locationsList input:checked'))
            .map(cb => JSON.parse(cb.value));

        const payload = {
            group_name: groupNameInput.value.trim(),
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
                showStatus(result.message || 'Group created successfully!', 'success');
                groupNameInput.value = '';
                validateForm();
            } else {
                showStatus(`Error: ${result.detail || 'Group creation failed.'}`, 'error');
                createGroupBtn.disabled = false;
            }
        } catch (error) {
            showStatus('Network error while creating group.', 'error');
            createGroupBtn.disabled = false;
        } finally {
            createGroupBtn.textContent = 'Create Custom Group';
        }
    });

    // Lifecycle Management
    const toggleBtn = document.getElementById('toggleGroupBBtn');
    const enableBtn = document.getElementById('enableGroupBBtn');
    const cleanupBtn = document.getElementById('cleanupGroupBBtn');

    async function handleLifecycle(endpoint, payload, button, loadingText, originalText) {
        button.disabled = true;
        button.textContent = loadingText;
        showStatus('');

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
            
            if (response.ok) {
                showStatus(result.message, 'success');
            } else {
                showStatus(`Error: ${result.detail || 'Action failed.'}`, 'error');
            }
        } catch (error) {
            showStatus('Network error.', 'error');
        } finally {
            button.textContent = originalText;
            validateForm();
        }
    }

    if (toggleBtn && enableBtn && cleanupBtn) {
        toggleBtn.addEventListener('click', () => {
            if(confirm('Are you sure you want to disable all Group B proxies?')) {
                handleLifecycle('/api/pasargard/lifecycle/toggle', { core_id: coreSelector.value, enable: false }, toggleBtn, 'Disabling...', 'Disable Group B');
            }
        });
        
        enableBtn.addEventListener('click', () => {
            handleLifecycle('/api/pasargard/lifecycle/toggle', { core_id: coreSelector.value, enable: true }, enableBtn, 'Enabling...', 'Enable Group B');
        });

        cleanupBtn.addEventListener('click', () => {
            if(confirm('WARNING: This will permanently delete all Group B configurations from this Core. Proceed?')) {
                handleLifecycle('/api/pasargard/lifecycle/cleanup', { core_id: coreSelector.value }, cleanupBtn, 'Cleaning Up...', 'Clean Up (Delete All)');
            }
        });
    }

    function showStatus(msg, type) {
        if (!msg) {
            statusMessage.className = 'status-message hidden';
            return;
        }
        statusMessage.textContent = msg;
        statusMessage.className = `status-message visible ${type}`;
    }

    // Service and Logs
    const fetchLogsBtn = document.getElementById('fetchLogsBtn');
    const restartServiceBtn = document.getElementById('restartServiceBtn');
    const stopServiceBtn = document.getElementById('stopServiceBtn');
    const logConsole = document.getElementById('logConsole');

    async function fetchLogs() {
        if (!fetchLogsBtn) return;
        try {
            const response = await fetch('/api/logs/wireproxy');
            const data = await response.json();
            if (response.ok && data.status === 'success') {
                const isScrolledToBottom = logConsole.scrollHeight - logConsole.clientHeight <= logConsole.scrollTop + 10;
                logConsole.textContent = data.logs || "No logs yet.";
                if (isScrolledToBottom) {
                    logConsole.scrollTop = logConsole.scrollHeight;
                }
            } else {
                logConsole.textContent = `Error fetching logs: ${data.logs || data.message}`;
            }
        } catch (e) {
            console.error("Error fetching logs in background.");
        }
    }

    if (fetchLogsBtn) {
        fetchLogsBtn.addEventListener('click', async () => {
            fetchLogsBtn.textContent = "Fetching...";
            await fetchLogs();
            fetchLogsBtn.textContent = "Refresh Logs";
            logConsole.scrollTop = logConsole.scrollHeight;
        });
    }

    if (restartServiceBtn) {
        restartServiceBtn.addEventListener('click', async () => {
            restartServiceBtn.disabled = true;
            restartServiceBtn.textContent = "Restarting...";
            try {
                const response = await fetch('/api/wireproxy/restart', { method: 'POST' });
                const data = await response.json();
                showStatus(data.message || "Restart signal sent.", data.status === 'success' ? 'success' : 'error');
            } catch (e) {
                showStatus("Network error", "error");
            } finally {
                restartServiceBtn.disabled = false;
                restartServiceBtn.textContent = "Restart Service";
            }
        });
    }

    if (stopServiceBtn) {
        stopServiceBtn.addEventListener('click', async () => {
            stopServiceBtn.disabled = true;
            stopServiceBtn.textContent = "Stopping...";
            try {
                const response = await fetch('/api/wireproxy/stop', { method: 'POST' });
                const data = await response.json();
                showStatus(data.message || "Stop signal sent.", data.status === 'success' ? 'success' : 'error');
            } catch (e) {
                showStatus("Network error", "error");
            } finally {
                stopServiceBtn.disabled = false;
                stopServiceBtn.textContent = "Stop Service";
            }
        });
    }

    // Auto-fetch logs on start and then every 2 seconds
    fetchLogs();
    setInterval(fetchLogs, 2000);

    // Start
    initializeApp();
});
