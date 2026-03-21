function setFTMSUI(active, stateText) {
    const statusEl = document.getElementById('ftms-status');
    const btn = document.getElementById('ftms-toggle');
    if (statusEl) {
        statusEl.innerText = t('ftms_status', { state: stateText });
        statusEl.style.color = active ? "var(--primary-accent)" : "#888";
    }
    if (btn) {
        btn.innerText = active ? t('ftms_off') : t('ftms_on');
        btn.classList.toggle('primary', !active);
    }
}

function fetchFTMSStatus() {
    fetch('/api/ftms/status')
        .then(r => r.json())
        .then(data => {
            const active = !!data.enabled;
            setFTMSUI(active, active ? t('ftms_enabled') : t('ftms_disabled'));
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
                btn.disabled = false;
                btn.classList.remove('loading');
            }
        });
}
