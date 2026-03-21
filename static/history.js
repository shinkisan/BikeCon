        function renderHistory(items) {
            const list = document.getElementById('history-list');
            if (!list) return;
            list.innerHTML = '';
            if (!items || items.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'history-empty';
                empty.innerText = t('history_empty');
                list.appendChild(empty);
                return;
            }
            items.forEach(it => {
                const card = document.createElement('div');
                card.className = 'history-card';
                const duration = formatDuration(it.active_duration_sec || 0);
                const start = formatDate(it.start_ts);
                const end = formatDate(it.end_ts);
                const avgPower = (it.avg_power == null) ? '--' : Math.round(it.avg_power);
                const avgSpeed = (it.avg_speed == null) ? '--' : it.avg_speed.toFixed(1);
                const avgRpm = (it.avg_rpm == null) ? '--' : Math.round(it.avg_rpm);
                const dist = (it.distance == null) ? '--' : `${it.distance}`;
                card.innerHTML = `
                    <div class="history-main">
                        <div class="history-time">${start} - ${end}</div>
                        <div class="history-sub">${t('history_duration', { duration })}</div>
                    </div>
                    <div class="history-metrics">
                        ${t('history_avg_power', { value: avgPower })}<br/>
                        ${t('history_avg_speed', { value: avgSpeed })}<br/>
                        ${t('history_avg_rpm', { value: avgRpm })}<br/>
                        ${t('history_distance', { value: dist })}
                    </div>
                `;
                list.appendChild(card);
            });
        }

        function refreshHistory() {
            const qs = buildHistoryQuery();
            fetch(`/api/sessions?page=1&page_size=20${qs}`)
                .then(r => r.json())
                .then(data => {
                    if (data && data.items) renderHistory(data.items);
                })
                .catch(() => {
                    renderHistory([]);
                });
        }

        function buildHistoryQuery() {
            const fromEl = document.getElementById('history-from');
            const toEl = document.getElementById('history-to');
            const fromVal = fromEl ? fromEl.value : '';
            const toVal = toEl ? toEl.value : '';
            const parts = [];
            if (fromVal) parts.push(`from=${encodeURIComponent(fromVal)}`);
            if (toVal) parts.push(`to=${encodeURIComponent(toVal)}`);
            return parts.length ? `&${parts.join('&')}` : '';
        }

        function applyHistoryFilter() {
            refreshHistory();
        }

        function clearHistoryFilter() {
            const fromEl = document.getElementById('history-from');
            const toEl = document.getElementById('history-to');
            if (fromEl) fromEl.value = '';
            if (toEl) toEl.value = '';
            refreshHistory();
        }

        function setDefaultHistoryRange() {
            const fromEl = document.getElementById('history-from');
            const toEl = document.getElementById('history-to');
            if (!fromEl || !toEl) return;
            const today = new Date();
            const toStr = today.toISOString().slice(0, 10);
            const fromDate = new Date(today.getTime() - 6 * 24 * 60 * 60 * 1000);
            const fromStr = fromDate.toISOString().slice(0, 10);
            if (!fromEl.value) fromEl.value = fromStr;
            if (!toEl.value) toEl.value = toStr;
        }
        function exportHistoryCSV() {
            const qs = buildHistoryQuery();
            fetch(`/api/sessions?page=1&page_size=200${qs}`)
                .then(r => r.json())
                .then(data => {
                    const items = (data && data.items) ? data.items : [];
                    if (items.length === 0) {
                        alert(t('csv_empty'));
                        return;
                    }
                    const rows = [];
                    rows.push([
                        t('csv_header_start'),
                        t('csv_header_end'),
                        t('csv_header_duration'),
                        t('csv_header_avg_power'),
                        t('csv_header_avg_speed'),
                        t('csv_header_avg_rpm'),
                        t('csv_header_distance')
                    ]);
                    items.forEach(it => {
                        const duration = formatDuration(it.active_duration_sec || 0);
                        const start = formatDate(it.start_ts);
                        const end = formatDate(it.end_ts);
                        const avgPower = (it.avg_power == null) ? '' : Math.round(it.avg_power);
                        const avgSpeed = (it.avg_speed == null) ? '' : Number(it.avg_speed).toFixed(1);
                        const avgRpm = (it.avg_rpm == null) ? '' : Math.round(it.avg_rpm);
                        const dist = (it.distance == null) ? '' : it.distance;
                        rows.push([start, end, duration, avgPower, avgSpeed, avgRpm, dist]);
                    });
                    const csv = rows.map(r => r.map(v => {
                        const s = String(v ?? '');
                        if (s.includes('"')) return `"${s.replace(/"/g, '""')}"`;
                        if (s.includes(',') || s.includes('\n')) return `"${s}"`;
                        return s;
                    }).join(',')).join('\n');
                    const blob = new Blob(["\uFEFF" + csv], { type: 'text/csv;charset=utf-8;' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    const ts = new Date();
                    const yyyy = ts.getFullYear();
                    const mm = String(ts.getMonth() + 1).padStart(2, '0');
                    const dd = String(ts.getDate()).padStart(2, '0');
                    const hh = String(ts.getHours()).padStart(2, '0');
                    const mi = String(ts.getMinutes()).padStart(2, '0');
                    a.href = url;
                    a.download = `bike_history_${yyyy}${mm}${dd}_${hh}${mi}.csv`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                })
                .catch(() => {
                    alert(t('csv_fail'));
                });
        }
