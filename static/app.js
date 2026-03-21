        let ws = null;
        let wsReconnectTimer = null;
        let wsRetryMs = 1000;
        const WS_RETRY_MAX_MS = 10000;
        let wsConnected = false;
        let lastWsDisconnectAt = 0;
        let lastWsOpenAt = 0;
        let wsNextRetryAt = 0;
        let wsStatusRefreshTimer = null;

        function handleWsMessage(event) {
            try {
                const msg = JSON.parse(event.data);
            if (msg.type === 'bike_link') {
                lastBikeMsgAt = Date.now();
                setConnectedState(!!msg.connected);
                applyUIState();
            } else if (msg.type === 'bike_data') {
                lastBikeMsgAt = Date.now();
                if (!bikeConnected) setConnectedState(true);
                    if (typeof msg.status === 'number') lastStatusCode = msg.status;
                    if (rpmEl) rpmEl.innerText = (typeof msg.rpm === 'number') ? msg.rpm : "--";
                    if (powerEl) powerEl.innerText = (typeof msg.power === 'number') ? msg.power : "--";
                    if (speedEl) speedEl.innerText = (typeof msg.speed === 'number') ? msg.speed : "--";
                    if (resistValEl && typeof msg.resistance === 'number') {
                        if (Date.now() - lastResistanceSetAt < RESISTANCE_SETTLE_MS) {
                            // 等待单车反应，暂不覆盖前端显示
                        } else {
                        resistValEl.innerText = msg.resistance;
                        if (!resistanceDragging) setDialValue(msg.resistance);
                        }
                    }
                    applyUIState();
            } else if (msg.type === 'bike_status') {
                lastBikeMsgAt = Date.now();
                setActiveState(!!msg.active);
                    if (typeof msg.status_name === 'string') lastStatusName = msg.status_name;
                    if (typeof msg.status_code === 'number') lastStatusCode = msg.status_code;
                    if (!bikeConnected) {
                        applyUIState();
                    } else if (!msg.active && rpmEl) {
                        // 状态变 Idle 时，前端也强制将显示值归零
                        rpmEl.innerText = "0";
                        applyUIState();
                    }
                } else if (msg.type === 'joycon_battery') {
                    const side = msg.side;
                    const percent = (typeof msg.percent === 'number') ? Math.max(0, Math.min(100, msg.percent)) : null;
                    if (side === 'LEFT' || side === 'RIGHT') {
                        if (msg.connected === false) {
                            joyconBattery[side] = null;
                        } else {
                            joyconBattery[side] = percent;
                        }
                        renderJoyconBattery();
                    }
            } else if (msg.type === 'session_state') {
                lastBikeMsgAt = Date.now();
                updateSessionState(msg.state, msg.active_duration_sec);
                setActiveState(msg.state === 'ACTIVE');
            }
            } catch (e) {}
        }

        function connectWebSocket() {
            ws = new WebSocket((location.protocol==='https:'?'wss:':'ws:') + "//" + location.host + "/ws");
            ws.onmessage = handleWsMessage;

            ws.onopen = () => {
                wsConnected = true;
                lastWsOpenAt = Date.now();
                wsRetryMs = 1000;
                wsNextRetryAt = 0;
                if (wsReconnectTimer) {
                    clearTimeout(wsReconnectTimer);
                    wsReconnectTimer = null;
                }
                applyUIState();
            };

            ws.onclose = () => {
                wsConnected = false;
                setConnectedState(false);
                lastWsDisconnectAt = Date.now();
                scheduleWsReconnect();
                applyUIState();
            };
        }

        connectWebSocket();
    
        // 1. 网页一打开，立刻通过 HTTP 读取配置 (比 WebSocket 更快更直接)
        window.onload = function() {
            if (typeof applyI18n === 'function') applyI18n();
            if (typeof setDashboardPlaceholders === 'function') setDashboardPlaceholders();
            if (typeof applyUIState === 'function') applyUIState();
            if (typeof setFTMSUI === 'function') setFTMSUI(false, t('ftms_unknown'));
            fetch('/api/config')
                .then(response => response.json())
                .then(config => {
                    console.log("配置已加载:", config);
                    if (config && config.language && typeof setLocale === 'function') {
                        setLocale(config.language, { noPersist: true });
                    }
                    // 更新下拉菜单
                    const sel = document.getElementById('bike-target');
                    if (sel) sel.value = config.target;
                    
                    // 更新滑杆
                    const range = document.getElementById('bike-max-rpm');
                    if (range) {
                        range.value = config.max_rpm;
                        document.getElementById('max-rpm-display').innerText = config.max_rpm;
                    }
                })
                .catch(err => console.error("加载配置失败:", err));

            setDefaultHistoryRange();
            fetchFTMSStatus();
            setInterval(fetchFTMSStatus, 5000);
        };

        // 新增：处理后端发来的数据
        const joyconBattery = { LEFT: null, RIGHT: null };
        function renderJoyconBattery() {
            const el = document.getElementById('joycon-battery');
            if (!el) return;
            const left = joyconBattery.LEFT;
            const right = joyconBattery.RIGHT;
            if (left == null && right == null) {
                el.innerText = t('joycon_label');
                el.style.color = "#777";
                return;
            }
            const parts = [];
            if (left != null) parts.push(`L ${left}%`);
            if (right != null) parts.push(`R ${right}%`);
            el.innerText = t('joycon_prefix') + " " + parts.join(" | ");
            el.style.color = "#ddd";
        }

        const sessionTimeEl = document.getElementById('session-time');
        let sessionTimer = null;
        let sessionBase = 0;
        let sessionStartClient = 0;
        let sessionState = null;

        function formatDuration(sec) {
            const s = Math.max(0, parseInt(sec, 10));
            const h = Math.floor(s / 3600);
            const m = Math.floor((s % 3600) / 60);
            const r = s % 60;
            if (h > 0) return `${h}:${m.toString().padStart(2,'0')}:${r.toString().padStart(2,'0')}`;
            return `${m.toString().padStart(2,'0')}:${r.toString().padStart(2,'0')}`;
        }

        function renderSessionTime(extraLabel) {
            if (!sessionTimeEl) return;
            const elapsed = sessionBase + Math.floor((Date.now() - sessionStartClient) / 1000);
            const time = formatDuration(elapsed);
            if (extraLabel) {
                sessionTimeEl.innerText = t('session_time_paused', { time });
            } else {
                sessionTimeEl.innerText = t('session_time', { time });
            }
        }

        function updateSessionState(state, activeDuration) {
            sessionState = state;
            sessionBase = activeDuration || 0;
            sessionStartClient = Date.now();
            if (sessionTimer) {
                clearInterval(sessionTimer);
                sessionTimer = null;
            }
            if (state === 'ACTIVE') {
                renderSessionTime();
                sessionTimer = setInterval(() => renderSessionTime(), 1000);
            } else if (state === 'PAUSED') {
                sessionStartClient = Date.now();
                renderSessionTime('paused');
            } else {
                if (sessionTimeEl) sessionTimeEl.innerText = t('session_time', { time: "--:--" });
            }
        }

        function refreshSessionTimeI18n() {
            if (!sessionTimeEl) return;
            if (sessionState === 'ACTIVE') {
                renderSessionTime();
            } else if (sessionState === 'PAUSED') {
                renderSessionTime('paused');
            } else {
                sessionTimeEl.innerText = t('session_time', { time: "--:--" });
            }
        }

        window.refreshSessionTimeI18n = refreshSessionTimeI18n;

        function formatDate(ts) {
            const d = new Date(ts * 1000);
            const yy = d.getFullYear();
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            const hh = String(d.getHours()).padStart(2, '0');
            const mi = String(d.getMinutes()).padStart(2, '0');
            return `${yy}-${mm}-${dd} ${hh}:${mi}`;
        }

        const resistanceDial = document.getElementById('resistance-dial');
        const resistDisplay = document.getElementById('resist-display');
        const resistValEl = document.getElementById('resist-val');
        const rpmEl = document.getElementById('rpm-val');
        const powerEl = document.getElementById('power-val');
        const speedEl = document.getElementById('speed-val');
        const btStatusEl = document.getElementById('bt-status');
        const resistBox = document.getElementById('resistance-box');
        const startBtn = document.getElementById('start-btn');
        let resistanceDragging = false;
        let bikeConnected = false;
        let bikeActive = false;
        let hasBikeEverConnected = false;
        let lastStatusName = null;
        let lastStatusCode = null;
        let lastBikeMsgAt = 0;
        let lastResistanceSetAt = 0;
        const BIKE_SYNC_GRACE_MS = 5000;
        const RESISTANCE_SETTLE_MS = 2000;

        function setMetricColor(el, isActive) {
            if (!el) return;
            el.style.color = isActive ? "var(--primary-accent)" : "#555";
        }

        function setDashboardPlaceholders() {
            if (rpmEl) rpmEl.innerText = "--";
            if (powerEl) powerEl.innerText = "--";
            if (speedEl) speedEl.innerText = "--";
            if (resistValEl) resistValEl.innerText = "--";
            if (resistDisplay) resistDisplay.innerText = "--";
            if (sessionTimeEl) sessionTimeEl.innerText = t('session_time', { time: "--:--" });
        }

        function getWsReconnectRemainingSec() {
            if (!wsNextRetryAt) return Math.max(1, Math.ceil(wsRetryMs / 1000));
            const remainMs = Math.max(0, wsNextRetryAt - Date.now());
            return Math.max(1, Math.ceil(remainMs / 1000));
        }

        function scheduleWsReconnect() {
            if (wsReconnectTimer) return;
            const retryDelay = wsRetryMs;
            wsNextRetryAt = Date.now() + retryDelay;
            wsReconnectTimer = setTimeout(() => {
                wsReconnectTimer = null;
                wsNextRetryAt = 0;
                connectWebSocket();
                wsRetryMs = Math.min(WS_RETRY_MAX_MS, retryDelay * 2);
            }, retryDelay);
        }

        function getStatusLabel() {
            if (!wsConnected) {
                return t('status_server_reconnect', { sec: getWsReconnectRemainingSec() });
            }
            if (!bikeConnected) {
                if (!hasBikeEverConnected) {
                    return t('status_sync');
                }
                if (!lastBikeMsgAt && lastWsOpenAt && (Date.now() - lastWsOpenAt < BIKE_SYNC_GRACE_MS)) {
                    return t('status_sync');
                }
                return t('status_bike_offline');
            }
            const name = lastStatusName;
            if (name === 'ACTIVE') return t('status_active');
            if (name === 'READY') return t('status_ready');
            if (name === 'TRANSITION') return t('status_transition');
            if (name === 'PAUSED') return t('status_paused');
            if (name === 'UNKNOWN') return t('status_unknown');
            if (lastStatusCode === 3) return t('status_active');
            if (lastStatusCode === 1) return t('status_ready');
            if (lastStatusCode === 2) return t('status_transition');
            if (lastStatusCode === 4) return t('status_paused');
            return bikeActive ? t('status_active') : t('status_ready');
        }

        function isTransitionState() {
            return lastStatusName === 'TRANSITION' || lastStatusCode === 2;
        }

        function applyUIState() {
            const active = bikeActive && bikeConnected;
            if (!wsConnected) {
                if (btStatusEl) {
                    btStatusEl.innerText = getStatusLabel();
                    btStatusEl.style.color = "#888";
                    btStatusEl.style.background = "none";
                }
                // 服务断线时，不再更新其它单车状态文案，避免闪烁
                return;
            } else
            if (startBtn) {
                const isPaused = (lastStatusName === 'PAUSED') || (lastStatusCode === 4);
                startBtn.innerText = isPaused ? t('btn_resume') : t('btn_start');
            }
            if (!bikeConnected) {
                if (btStatusEl) {
                    btStatusEl.innerText = getStatusLabel();
                    btStatusEl.style.color = "#f44336";
                    btStatusEl.style.background = "none";
                }
            } else {
                if (btStatusEl) {
                    btStatusEl.innerText = getStatusLabel();
                    if (bikeActive) {
                        btStatusEl.style.color = "var(--primary-accent)";
                        btStatusEl.style.background = "rgba(0, 255, 136, 0.1)";
                    } else if (isTransitionState()) {
                        btStatusEl.style.color = "#ffca28";
                        btStatusEl.style.background = "rgba(255, 202, 40, 0.12)";
                    } else {
                        btStatusEl.style.color = "#888";
                        btStatusEl.style.background = "none";
                    }
                }
            }

            setMetricColor(rpmEl, active);
            setMetricColor(powerEl, active);
            setMetricColor(speedEl, active);
            setMetricColor(resistValEl, active);
            setMetricColor(resistDisplay, active);

            if (resistBox) resistBox.classList.toggle('inactive', !active);
            if (resistanceDial) {
                resistanceDial.disabled = !active;
                if (active) {
                    updateDialBackground(resistanceDial);
                } else {
                    resistanceDial.style.background = "#333";
                }
            }
        }

        function setConnectedState(connected) {
            if (bikeConnected === connected) return;
            bikeConnected = connected;
            if (!connected) {
                setDashboardPlaceholders();
            } else {
                hasBikeEverConnected = true;
            }
            applyUIState();
        }

        function setActiveState(active) {
            if (bikeActive === active) return;
            bikeActive = active;
            applyUIState();
        }

        setConnectedState(false);

        if (!wsStatusRefreshTimer) {
            wsStatusRefreshTimer = setInterval(() => {
                if (!wsConnected) {
                    applyUIState();
                }
            }, 1000);
        }

        function updateDialBackground(el) {
            if (!el) return;
            const min = parseInt(el.min, 10);
            const max = parseInt(el.max, 10);
            const val = parseInt(el.value, 10);
            const pct = Math.max(0, Math.min(100, ((val - min) / (max - min)) * 100));
            el.style.background = `linear-gradient(90deg, #00ff88 0%, #0f6 ${pct}%, #2b2b2b ${pct}%)`;
        }

        function setDialValue(val) {
            if (!resistanceDial) return;
            const v = Math.max(parseInt(resistanceDial.min, 10), Math.min(parseInt(resistanceDial.max, 10), parseInt(val, 10)));
            resistanceDial.value = v;
            if (resistDisplay) resistDisplay.innerText = v;
            if (bikeActive && bikeConnected) {
                updateDialBackground(resistanceDial);
            } else {
                resistanceDial.style.background = "#333";
            }
        }

        if (resistanceDial) {
            if (bikeActive && bikeConnected) {
                updateDialBackground(resistanceDial);
            } else {
                resistanceDial.style.background = "#333";
            }
            resistanceDial.addEventListener('pointerdown', () => { resistanceDragging = true; });
            resistanceDial.addEventListener('pointerup', () => { resistanceDragging = false; });
            resistanceDial.addEventListener('pointercancel', () => { resistanceDragging = false; });
            resistanceDial.addEventListener('input', (e) => {
                const v = e.target.value;
                if (resistDisplay) resistDisplay.innerText = v;
                if (resistValEl) resistValEl.innerText = v;
                if (bikeActive && bikeConnected) updateDialBackground(resistanceDial);
            });
            resistanceDial.addEventListener('change', (e) => {
                const v = parseInt(e.target.value, 10);
                sendResistance(v);
            });
        }

        // 新增：向后端发送单车配置
        function sendBikeConfig() {
            const target = document.getElementById('bike-target').value;
            const maxRpm = document.getElementById('bike-max-rpm').value;
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: 'bike_config',
                    target: target,
                    max_rpm: parseInt(maxRpm)
                }));
            }
        }
        function btn(id, val) {
            if(val && navigator.vibrate) navigator.vibrate(15);
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({type:'btn', source:'virtual', id:id, val:val}));
            }
        }

        function trigger(lr, val) {
            if(val && navigator.vibrate) navigator.vibrate(15);
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({type:'trigger', source:'virtual', lr:lr, val:val}));
            }
        }

        function sendControl(action) {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({type:'control', action: action}));
            }
        }

        function sendResistance(level) {
            if (!bikeActive || !bikeConnected) return;
            if (ws.readyState === WebSocket.OPEN) {
                lastResistanceSetAt = Date.now();
                if (resistValEl) resistValEl.innerText = level;
                if (!resistanceDragging) setDialValue(level);
                ws.send(JSON.stringify({type:'set_resistance', level: parseInt(level)}));
            }
        }
        
        function sendSource(val) {
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type:'source', val:val}));
        }

        function sendAxis(stick, x, y) {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({type:'axis', source:'virtual', stick: stick, x: x, y: y}));
            }
        }

        // --- 摇杆逻辑 (视觉/数据坐标解耦修复版) ---
        function switchTab(id, el) {
            document.querySelectorAll('.tab-content').forEach(d => d.classList.remove('active'));
            document.querySelectorAll('.nav-btn').forEach(n => n.classList.remove('active'));
            document.getElementById('tab-' + id).classList.add('active');
            el.classList.add('active');
        }
        document.addEventListener('contextmenu', e => e.preventDefault());

        window.sendAxis = sendAxis;
        window.sendBikeConfig = sendBikeConfig;
        window.btn = btn;
        window.trigger = trigger;
        window.switchTab = switchTab;
    
