(function () {
    const I18N = {
        zh: {
            app_title: "BikeCon 控制台",
            tab_bike: "🚴 单车",
            tab_gamepad: "🎮 手柄",
            tab_history: "📜 记录",
            tab_settings: "⚙️ 设置",

            metric_rpm: "踏频",
            metric_power: "功率",
            metric_speed: "速度",
            metric_resistance: "阻力",

            status_sync: "获取单车状态中…",
            status_server_reconnect: "正在重连服务器，{sec}秒后重试…",
            status_bike_offline: "单车离线，重连中……",
            status_active: "骑行中",
            status_ready: "待机",
            status_transition: "准备",
            status_paused: "暂停中",
            status_unknown: "未知",

            session_time: "本次时长 {time}",
            session_time_paused: "本次时长 {time} (暂停)",

            btn_start: "开始",
            btn_resume: "恢复",
            btn_pause: "暂停",
            btn_stop: "停止",

            resistance_control: "阻力调节",

            history_title: "历史记录",
            history_refresh: "刷新",
            history_export: "导出 CSV",
            history_filter: "筛选",
            history_clear: "清空",
            history_empty: "暂无记录",
            history_duration: "时长 {duration}",
            history_avg_power: "均功率 {value}W",
            history_avg_speed: "均时速 {value}km/h",
            history_avg_rpm: "均踏频 {value}RPM",
            history_distance: "里程 {value} m",

            csv_header_start: "开始时间",
            csv_header_end: "结束时间",
            csv_header_duration: "时长",
            csv_header_avg_power: "均功率(W)",
            csv_header_avg_speed: "均时速(km/h)",
            csv_header_avg_rpm: "均踏频(RPM)",
            csv_header_distance: "里程(m)",
            csv_empty: "暂无可导出的记录",
            csv_fail: "导出失败，请稍后重试",

            ftms_title: "FTMS 服务",
            ftms_status: "状态：{state}",
            ftms_enabled: "已启用",
            ftms_disabled: "已禁用",
            ftms_unknown: "未知",
            ftms_on: "开启",
            ftms_off: "关闭",

            bike_target_label: "🚴 单车映射目标",
            bike_max_rpm_label: "最大 RPM (满量程)",

            language_label: "语言（Language）",
            language_zh: "中文",
            language_en: "English",
            joycon_label: "JoyCon: --",
            joycon_prefix: "JoyCon：",

            gamepad_exit: "✕ 退出",

            opt_disabled: "-- 禁用映射 --",
            opt_stick_group: "模拟摇杆",
            opt_trigger_group: "线性扳机",
            opt_button_group: "数字按钮",
            opt_ly_inv: "左摇杆 - 上推 (前进)",
            opt_ly: "左摇杆 - 下推",
            opt_lx_inv: "左摇杆 - 左推",
            opt_lx: "左摇杆 - 右推",
            opt_ry_inv: "右摇杆 - 上推 (油门)",
            opt_ry: "右摇杆 - 下推",
            opt_lt: "左扳机 (LT)",
            opt_rt: "右扳机 (RT)",
            opt_btn_a: "按键 A",
            opt_btn_b: "按键 B",
            opt_btn_x: "按键 X",
            opt_btn_y: "按键 Y",
            opt_btn_lb: "左肩键 (LB)",
            opt_btn_rb: "右肩键 (RB)",
            opt_btn_start: "START",
            opt_btn_select: "SELECT"
        },
        en: {
            app_title: "BikeCon Console",
            tab_bike: "🚴 Bike",
            tab_gamepad: "🎮 Gamepad",
            tab_history: "📜 History",
            tab_settings: "⚙️ Settings",

            metric_rpm: "Cadence",
            metric_power: "Power",
            metric_speed: "Speed",
            metric_resistance: "Resistance",

            status_sync: "Syncing bike status…",
            status_server_reconnect: "Reconnecting server, retry in {sec}s…",
            status_bike_offline: "Bike offline, reconnecting…",
            status_active: "Riding",
            status_ready: "Ready",
            status_transition: "Preparing",
            status_paused: "Paused",
            status_unknown: "Unknown",

            session_time: "Session {time}",
            session_time_paused: "Session {time} (Paused)",

            btn_start: "Start",
            btn_resume: "Resume",
            btn_pause: "Pause",
            btn_stop: "Stop",

            resistance_control: "Resistance",

            history_title: "History",
            history_refresh: "Refresh",
            history_export: "Export CSV",
            history_filter: "Filter",
            history_clear: "Clear",
            history_empty: "No records",
            history_duration: "Duration {duration}",
            history_avg_power: "Avg Power {value}W",
            history_avg_speed: "Avg Speed {value}km/h",
            history_avg_rpm: "Avg Cadence {value}RPM",
            history_distance: "Distance {value} m",

            csv_header_start: "Start",
            csv_header_end: "End",
            csv_header_duration: "Duration",
            csv_header_avg_power: "Avg Power(W)",
            csv_header_avg_speed: "Avg Speed(km/h)",
            csv_header_avg_rpm: "Avg Cadence(RPM)",
            csv_header_distance: "Distance(m)",
            csv_empty: "No records to export",
            csv_fail: "Export failed, try again",

            ftms_title: "FTMS Service",
            ftms_status: "Status: {state}",
            ftms_enabled: "Enabled",
            ftms_disabled: "Disabled",
            ftms_unknown: "Unknown",
            ftms_on: "Enable",
            ftms_off: "Disable",

            bike_target_label: "🚴 Bike Mapping Target",
            bike_max_rpm_label: "Max RPM",

            language_label: "Language",
            language_zh: "中文",
            language_en: "English",
            joycon_label: "JoyCon: --",
            joycon_prefix: "JoyCon:",

            gamepad_exit: "✕ Exit",

            opt_disabled: "-- Disable Mapping --",
            opt_stick_group: "Analog Sticks",
            opt_trigger_group: "Analog Triggers",
            opt_button_group: "Digital Buttons",
            opt_ly_inv: "Left Stick - Up (Forward)",
            opt_ly: "Left Stick - Down",
            opt_lx_inv: "Left Stick - Left",
            opt_lx: "Left Stick - Right",
            opt_ry_inv: "Right Stick - Up (Throttle)",
            opt_ry: "Right Stick - Down",
            opt_lt: "Left Trigger (LT)",
            opt_rt: "Right Trigger (RT)",
            opt_btn_a: "Button A",
            opt_btn_b: "Button B",
            opt_btn_x: "Button X",
            opt_btn_y: "Button Y",
            opt_btn_lb: "Left Shoulder (LB)",
            opt_btn_rb: "Right Shoulder (RB)",
            opt_btn_start: "START",
            opt_btn_select: "SELECT"
        }
    };

    let currentLocale = 'zh';

    function detectLocale() {
        const saved = localStorage.getItem('bikecon_locale');
        if (saved && I18N[saved]) return saved;
        const nav = (navigator.language || '').toLowerCase();
        if (nav.startsWith('en')) return 'en';
        return 'zh';
    }

    function t(key, vars) {
        const dict = I18N[currentLocale] || I18N.zh;
        let val = dict[key] ?? I18N.zh[key] ?? key;
        if (typeof val === 'function') return val(vars, currentLocale);
        if (!vars) return val;
        return val.replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null ? vars[k] : ''));
    }

    function applyI18n() {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            if (!key) return;
            el.textContent = t(key);
        });
        document.querySelectorAll('[data-i18n-label]').forEach(el => {
            const key = el.getAttribute('data-i18n-label');
            if (!key) return;
            el.setAttribute('label', t(key));
        });
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            if (!key) return;
            el.setAttribute('title', t(key));
        });
        const langSel = document.getElementById('lang-select');
        if (langSel) langSel.value = currentLocale;
    }

    function setLocale(locale, opts = {}) {
        if (!I18N[locale]) return;
        currentLocale = locale;
        if (!opts.noPersist) {
            localStorage.setItem('bikecon_locale', locale);
            fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ language: locale })
            }).catch(() => {});
        }
        applyI18n();
        if (typeof window.applyUIState === 'function') window.applyUIState();
        if (typeof window.refreshSessionTimeI18n === 'function') window.refreshSessionTimeI18n();
        if (typeof window.refreshHistory === 'function') window.refreshHistory();
        if (typeof window.fetchFTMSStatus === 'function') window.fetchFTMSStatus();
    }

    currentLocale = detectLocale();

    window.I18N = I18N;
    window.t = t;
    window.setLocale = setLocale;
    window.applyI18n = applyI18n;
    window.getLocale = () => currentLocale;
})();
