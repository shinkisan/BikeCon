        function initStick(elmId, side) {
            const zone = document.getElementById(elmId);
            const knob = zone.querySelector('.stick-knob');
            const gamepadContainer = document.getElementById('landscape-gamepad');

            let lastSend = 0;

            function update(dx, dy, force=false) {
                const now = Date.now();
                if (!force && now - lastSend < 20) return;
                lastSend = now;

                // --- 1. 计算最大物理移动半径 ---
                // zone.width/2 (底座半径) - knob.width/2 (球半径)
                // 这样当球边缘碰到底座边缘时，圆心移动距离就是 maxDist
                const maxDist = zone.offsetWidth / 2 - knob.offsetWidth / 2;
                
                if (maxDist <= 0) return;

                // --- 2. 视觉层修正 (Visual Layer) ---
                // 让摇杆球看起来跟随手指
                let visualX = dx;
                let visualY = dy;

                if (gamepadContainer.classList.contains('rotated')) {
                    // 竖屏强转模式下，视觉坐标系旋转了 90度
                    // 物理右(dx+) -> 视觉上(vy-)
                    // 物理下(dy+) -> 视觉右(vx+)
                    visualX = dy;
                    visualY = -dx;
                }
                knob.style.transform = `translate(calc(-50% + ${visualX}px), calc(-50% + ${visualY}px))`;

                // --- 3. 数据层修正 (Data Layer) ---
                // 核心修正：数据层永远对应物理手势！
                // 物理向右推 (dx > 0) -> 发送 X > 128
                // 物理向下推 (dy > 0) -> 发送 Y > 128
                
                // 这里不需要检测 rotated！
                // 因为当用户竖屏玩时，他往“物理右侧”推，就是想让游戏角色往右走
                // 即使屏幕画面是转过来的，操作直觉依然是基于物理方向的
                
                // 唯一需要注意的是，如果用户是横着拿手机（顶部朝左/右）
                // 那么物理坐标系本身相对于人眼就变了。
                
                // 假设用户是把手机横过来拿（Home键在右）：
                // 物理上(dy-) -> 人眼左 -> Game Left (X-)
                // 物理右(dx+) -> 人眼上 -> Game Up (Y-)
                
                // 让我们根据之前的反馈来修正：
                // "右摇杆往右，传入了Y轴数据，但应该是X轴"
                // 这说明之前代码里的 finalX = y 把轴换错了。
                
                let gameX = dx;
                let gameY = dy;

                if (gamepadContainer.classList.contains('rotated')) {
                    gameX = dy;
                    gameY = -dx;
                }

                // 数值归一化 (0-255)
                // 确保 range 能够达到 0 和 255
                // 使用 clamp 防止溢出
                const normalize = (val) => {
                    let v = ((val / maxDist) + 1) / 2 * 255;
                    return Math.max(0, Math.min(255, Math.round(v)));
                };

                const valX = normalize(gameX);
                const valY = normalize(gameY);
                
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({type: 'axis', source: 'virtual', stick: side, x: valX, y: valY}));
                }
            }

            zone.addEventListener('touchstart', e => {
                e.preventDefault();
                const rect = zone.getBoundingClientRect();
                const centerX = rect.left + rect.width / 2;
                const centerY = rect.top + rect.height / 2;
                
                // 重新计算 maxDist，确保准确
                // 关键修正：必须减去球体半径！否则推不到顶！
                const maxDist = zone.offsetWidth / 2 - knob.offsetWidth / 2;

                const moveHandler = (em) => {
                    const tm = em.targetTouches[0];
                    let dx = tm.clientX - centerX;
                    let dy = tm.clientY - centerY;
                    
                    // 限制在圆形范围内
                    const dist = Math.sqrt(dx*dx + dy*dy);
                    if (dist > maxDist) {
                        const ratio = maxDist / dist;
                        dx *= ratio; dy *= ratio;
                    }
                    
                    update(dx, dy);
                };
                const endHandler = () => {
                    update(0, 0, true);
                    zone.removeEventListener('touchmove', moveHandler);
                    zone.removeEventListener('touchend', endHandler);
                };
                zone.addEventListener('touchmove', moveHandler);
                zone.addEventListener('touchend', endHandler);
            });
        }
        
        setTimeout(() => {
            initStick('stick-l', 'left');
            initStick('stick-r', 'right');
        }, 500);

        setInterval(() => {
            if (!bikeConnected) {
                applyUIState();
            }
        }, 1000);

        function enterGamepad() {
            const gp = document.getElementById('landscape-gamepad');
            const pt = document.getElementById('portrait-layout');
            gp.style.display = 'block'; pt.style.display = 'none';
            if(document.documentElement.requestFullscreen) document.documentElement.requestFullscreen();
            checkRot();
        }
        function exitGamepad() {
            const gp = document.getElementById('landscape-gamepad');
            const pt = document.getElementById('portrait-layout');
            gp.style.display = 'none'; pt.style.display = 'flex';
            gp.classList.remove('rotated');
            if(document.exitFullscreen) document.exitFullscreen();
        }
        function checkRot() {
            const gp = document.getElementById('landscape-gamepad');
            // 判断逻辑：只要高大于宽，就加旋转类
            if(window.innerHeight > window.innerWidth) gp.classList.add('rotated');
            else gp.classList.remove('rotated');
        }
        window.addEventListener('resize', () => { if(document.getElementById('landscape-gamepad').style.display === 'block') checkRot(); });
