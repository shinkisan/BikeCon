function setFTMSUI(active, stateText, opts = {}) {
    const forcedOff = !!opts.forcedOff;
    const statusEl = document.getElementById('ftms-status');
    const btn = document.getElementById('ftms-toggle');
    if (statusEl) {
        statusEl.innerText = t('ftms_status', { state: stateText });
        if (forcedOff) {
            statusEl.style.color = "#ffca28";
        } else {
            statusEl.style.color = active ? "var(--primary-accent)" : "#888";
        }
    }
    if (btn) {
        btn.dataset.forcedOff = forcedOff ? "1" : "0";
        if (forcedOff) {
            btn.innerText = t('ftms_forced_button');
            btn.classList.remove('primary');
            btn.disabled = true;
        } else {
            btn.innerText = active ? t('ftms_off') : t('ftms_on');
            btn.classList.toggle('primary', !active);
            if (!btn.classList.contains('loading')) {
                btn.disabled = false;
            }
        }
    }
}

function fetchFTMSStatus() {
    fetch('/api/ftms/status')
        .then(r => r.json())
        .then(data => {
            const forcedOff = !!data.forced_off;
            const active = !!data.enabled;
            if (forcedOff) {
                setFTMSUI(false, t('ftms_forced_off'), { forcedOff: true });
            } else {
                setFTMSUI(active, active ? t('ftms_enabled') : t('ftms_disabled'));
            }
        })
        .catch(() => {
            setFTMSUI(false, t('ftms_unknown'));
        });
}

function toggleFTMS() {
    const btn = document.getElementById('ftms-toggle');
    if (btn) {
        btn.disabled = true;
        btn.classList.add('loading');
    }
    fetch('/api/ftms/status')
        .then(r => r.json())
        .then(data => {
            if (data && data.forced_off) {
                setFTMSUI(false, t('ftms_forced_off'), { forcedOff: true });
                return null;
            }
            const active = !!data.enabled;
            const endpoint = active ? '/api/ftms/stop' : '/api/ftms/start';
            return fetch(endpoint, { method: 'POST' });
        })
        .then(() => {
            setTimeout(fetchFTMSStatus, 300);
        })
        .catch(() => {
            setFTMSUI(false, t('ftms_unknown'));
        })
        .finally(() => {
            if (btn) {
                btn.classList.remove('loading');
                btn.disabled = btn.dataset.forcedOff === "1";
            }
        });
}
