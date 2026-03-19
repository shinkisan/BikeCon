import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import subprocess
from pathlib import Path

# 配置路径
BASE_DIR = Path("/opt/BikeCon")
CONFIG_FILE = Path("/etc/BikeCon/config.json")
STATE_DIR = Path("/var/lib/BikeCon")
DB_FILE = STATE_DIR / "sessions.db"
SESSION_STATE_FILE = STATE_DIR / "session_state.json"

# Socket 路径
WEBAPP_SOCKET = "/var/run/BikeCon/webapp.sock"  # 接收来自 bike_service 的数据
MIXER_SOCKET = "/var/run/BikeCon/mixer.sock"    # 发送指令给 mixer (控制源切换等)
CONTROL_SOCKET = "/var/run/BikeCon/control.sock"  # 发送控制指令给 bike_service

# 确保运行时目录存在
os.makedirs("/var/run/BikeCon", exist_ok=True)
os.makedirs("/var/lib/BikeCon", exist_ok=True)

# --- 全局状态 ---
active_websockets: Set[WebSocket] = set()

class SessionStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = None
        self._lock = asyncio.Lock()
        self._ensure_db()

    def _ensure_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_ts INTEGER NOT NULL,
                    end_ts INTEGER NOT NULL,
                    active_duration_sec INTEGER NOT NULL,
                    avg_rpm REAL,
                    max_rpm REAL,
                    avg_power REAL,
                    max_power REAL,
                    avg_speed REAL,
                    distance REAL,
                    calories REAL,
                    resist_start INTEGER,
                    resist_end INTEGER,
                    resist_avg REAL,
                    aborted INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_end ON sessions(end_ts)")
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN avg_speed REAL")
            except sqlite3.OperationalError:
                pass
            conn.commit()
        finally:
            conn.close()

    async def insert_session(self, row: dict):
        async with self._lock:
            if not self._conn:
                self._conn = sqlite3.connect(self.db_path)
            cols = ", ".join(row.keys())
            placeholders = ", ".join(["?"] * len(row))
            self._conn.execute(
                f"INSERT INTO sessions ({cols}) VALUES ({placeholders})",
                list(row.values())
            )
            self._conn.commit()

    async def list_sessions(self, start_ts=None, end_ts=None, page=1, page_size=20):
        async with self._lock:
            if not self._conn:
                self._conn = sqlite3.connect(self.db_path)
                self._conn.row_factory = sqlite3.Row
            clauses = []
            params = []
            if start_ts is not None:
                clauses.append("start_ts >= ?")
                params.append(start_ts)
            if end_ts is not None:
                clauses.append("start_ts < ?")
                params.append(end_ts)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            count_row = self._conn.execute(
                f"SELECT COUNT(1) as cnt FROM sessions {where}",
                params
            ).fetchone()
            total = count_row["cnt"] if count_row else 0
            offset = max(0, (page - 1) * page_size)
            rows = self._conn.execute(
                f"SELECT * FROM sessions {where} ORDER BY start_ts DESC LIMIT ? OFFSET ?",
                params + [page_size, offset]
            ).fetchall()
            return total, [dict(r) for r in rows]

    async def get_session(self, session_id: int):
        async with self._lock:
            if not self._conn:
                self._conn = sqlite3.connect(self.db_path)
                self._conn.row_factory = sqlite3.Row
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,)
            ).fetchone()
            return dict(row) if row else None

class SessionTracker:
    def __init__(self, store: SessionStore, state_path: Path):
        self.store = store
        self.state_path = state_path
        self.last_status = None
        self.session = None
        self._recover_if_needed()

    def _now(self):
        return int(time.time())

    def _persist_state(self):
        if not self.session:
            try:
                if self.state_path.exists():
                    self.state_path.unlink()
            except Exception:
                pass
            return
        try:
            payload = {
                "start_ts": self.session["start_ts"],
                "active_duration_sec": self.session["active_duration_sec"],
                "active_start_ts": self.session.get("active_start_ts"),
                "status": self.session.get("status")
            }
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(payload))
        except Exception:
            pass

    def _recover_if_needed(self):
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            start_ts = int(data.get("start_ts", 0))
            active_duration_sec = int(data.get("active_duration_sec", 0))
            active_start_ts = data.get("active_start_ts")
            status = data.get("status")
            now = self._now()
            if active_start_ts:
                active_duration_sec += max(0, now - int(active_start_ts))
            if active_duration_sec >= 60 and start_ts > 0:
                row = {
                    "start_ts": start_ts,
                    "end_ts": now,
                    "active_duration_sec": active_duration_sec,
                    "avg_rpm": None,
                    "max_rpm": None,
                    "avg_power": None,
                    "max_power": None,
                    "avg_speed": None,
                    "distance": None,
                    "calories": None,
                    "resist_start": None,
                    "resist_end": None,
                    "resist_avg": None,
                    "aborted": 1
                }
                conn = sqlite3.connect(self.store.db_path)
                try:
                    cols = ", ".join(row.keys())
                    placeholders = ", ".join(["?"] * len(row))
                    conn.execute(
                        f"INSERT INTO sessions ({cols}) VALUES ({placeholders})",
                        list(row.values())
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass
        finally:
            try:
                self.state_path.unlink()
            except Exception:
                pass

    def _current_active_duration(self):
        if not self.session:
            return 0
        dur = self.session["active_duration_sec"]
        if self.session.get("status") == "ACTIVE" and self.session.get("active_start_ts"):
            dur += max(0, self._now() - self.session["active_start_ts"])
        return dur

    def session_state_payload(self):
        if not self.session:
            return {"type": "session_state", "state": "READY", "active_duration_sec": 0, "start_ts": None}
        return {
            "type": "session_state",
            "state": self.session.get("status", "READY"),
            "active_duration_sec": self._current_active_duration(),
            "start_ts": self.session.get("start_ts")
        }

    def _ensure_session(self):
        if not self.session:
            self.session = {
                "start_ts": self._now(),
                "active_duration_sec": 0,
                "active_start_ts": None,
                "status": None,
                "sum_rpm": 0.0,
                "cnt_rpm": 0,
                "max_rpm": None,
                "sum_power": 0.0,
                "cnt_power": 0,
                "max_power": None,
                "sum_speed": 0.0,
                "cnt_speed": 0,
                "max_speed": None,
                "resist_sum": 0.0,
                "resist_cnt": 0,
                "resist_start": None,
                "resist_end": None,
                "distance": None,
                "calories": None,
            }

    def on_status(self, status_name: str):
        prev = self.last_status
        self.last_status = status_name
        if prev == "TRANSITION" and status_name == "ACTIVE":
            self._ensure_session()
            self.session["status"] = "ACTIVE"
            self.session["active_start_ts"] = self._now()
            self._persist_state()
            return True
        if prev == "ACTIVE" and status_name == "PAUSED":
            if self.session and self.session.get("active_start_ts"):
                self.session["active_duration_sec"] += max(0, self._now() - self.session["active_start_ts"])
                self.session["active_start_ts"] = None
            if self.session:
                self.session["status"] = "PAUSED"
            self._persist_state()
            return True
        if prev == "PAUSED" and status_name == "ACTIVE":
            if self.session:
                self.session["status"] = "ACTIVE"
                self.session["active_start_ts"] = self._now()
                self._persist_state()
            return True
        if status_name == "READY" and prev in ("ACTIVE", "PAUSED"):
            if self.session:
                if prev == "ACTIVE" and self.session.get("active_start_ts"):
                    self.session["active_duration_sec"] += max(0, self._now() - self.session["active_start_ts"])
                self.session["active_start_ts"] = None
                self.session["status"] = "READY"
                self._persist_state()
            asyncio.create_task(self._end_session(aborted=0))
            return True
        return False

    async def _end_session(self, aborted: int):
        if not self.session:
            return
        if self.session.get("status") == "ACTIVE" and self.session.get("active_start_ts"):
            self.session["active_duration_sec"] += max(0, self._now() - self.session["active_start_ts"])
        self.session["active_start_ts"] = None
        self.session["status"] = "READY"
        active_duration = self.session["active_duration_sec"]
        if active_duration >= 60:
            avg_rpm = (self.session["sum_rpm"] / self.session["cnt_rpm"]) if self.session["cnt_rpm"] else None
            avg_power = (self.session["sum_power"] / self.session["cnt_power"]) if self.session["cnt_power"] else None
            avg_speed = (self.session["sum_speed"] / self.session["cnt_speed"]) if self.session["cnt_speed"] else None
            resist_avg = (self.session["resist_sum"] / self.session["resist_cnt"]) if self.session["resist_cnt"] else None
            row = {
                "start_ts": self.session["start_ts"],
                "end_ts": self._now(),
                "active_duration_sec": active_duration,
                "avg_rpm": avg_rpm,
                "max_rpm": self.session["max_rpm"],
                "avg_power": avg_power,
                "max_power": self.session["max_power"],
                "avg_speed": avg_speed,
                "distance": self.session["distance"],
                "calories": self.session["calories"],
                "resist_start": self.session["resist_start"],
                "resist_end": self.session["resist_end"],
                "resist_avg": resist_avg,
                "aborted": aborted
            }
            await self.store.insert_session(row)
        self.session = None
        self._persist_state()

    def on_data(self, msg: dict):
        if not self.session or self.session.get("status") != "ACTIVE":
            return
        rpm = msg.get("rpm")
        if isinstance(rpm, (int, float)):
            self.session["sum_rpm"] += rpm
            self.session["cnt_rpm"] += 1
            self.session["max_rpm"] = rpm if self.session["max_rpm"] is None else max(self.session["max_rpm"], rpm)
        power = msg.get("power")
        if isinstance(power, (int, float)):
            self.session["sum_power"] += power
            self.session["cnt_power"] += 1
            self.session["max_power"] = power if self.session["max_power"] is None else max(self.session["max_power"], power)
        speed = msg.get("speed")
        if isinstance(speed, (int, float)):
            self.session["sum_speed"] += speed
            self.session["cnt_speed"] += 1
            self.session["max_speed"] = speed if self.session["max_speed"] is None else max(self.session["max_speed"], speed)
        resistance = msg.get("resistance")
        if isinstance(resistance, (int, float)):
            if self.session["resist_start"] is None:
                self.session["resist_start"] = int(resistance)
            self.session["resist_end"] = int(resistance)
            self.session["resist_sum"] += resistance
            self.session["resist_cnt"] += 1
        if "distance" in msg:
            self.session["distance"] = msg.get("distance")
        if "calories" in msg:
            self.session["calories"] = msg.get("calories")
        self._persist_state()

# --- 1. Session Store/Tracker ---
session_store = SessionStore(DB_FILE)
session_tracker = SessionTracker(session_store, SESSION_STATE_FILE)

# --- 2. 后台监听任务：接收单车数据并广播 ---
async def socket_listener():
    """监听来自 bike_service.py 的长连接数据流"""
    # 清理旧的 Socket 文件
    if os.path.exists(WEBAPP_SOCKET):
        os.remove(WEBAPP_SOCKET)

    async def handle_client(reader, writer):
        try:
            while True:
                # 按行读取，匹配 bike_service 发出的 \n
                line = await reader.readline()
                if not line: break
                
                # 解码并广播
                data_str = line.decode().strip()
                if not data_str: continue

                extra_payloads = []
                try:
                    msg = json.loads(data_str)
                    msg_type = msg.get("type")
                    if msg_type == "bike_status":
                        changed = session_tracker.on_status(msg.get("status_name"))
                        if changed:
                            extra_payloads.append(session_tracker.session_state_payload())
                    elif msg_type == "bike_data":
                        session_tracker.on_data(msg)
                        if session_tracker.session:
                            extra_payloads.append(session_tracker.session_state_payload())
                except Exception:
                    pass

                # 广播给所有连接的浏览器
                try:
                    if active_websockets:
                        tasks = [ws.send_text(data_str) for ws in active_websockets]
                        for payload in extra_payloads:
                            tasks.extend([ws.send_text(json.dumps(payload)) for ws in active_websockets])
                        await asyncio.gather(*tasks, return_exceptions=True)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    # 启动 Unix Socket 服务
    server = await asyncio.start_unix_server(handle_client, path=WEBAPP_SOCKET)
    # 修改权限，确保 bike_service (root/user) 能写入
    os.chmod(WEBAPP_SOCKET, 0o666)
    
    print(f"[WebApp] 监听服务已启动: {WEBAPP_SOCKET}")
    
    async with server:
        await server.serve_forever()

# --- 2. 异步发送指令给 Mixer (长连接客户端) ---
class MixerClient:
    def __init__(self):
        self.writer = None

    async def send(self, data):
        """发送 JSON 数据到 Mixer"""
        if not self.writer or self.writer.transport.is_closing():
            try:
                _, self.writer = await asyncio.open_unix_connection(MIXER_SOCKET)
            except Exception:
                self.writer = None
                return

        try:
            self.writer.write(json.dumps(data).encode() + b'\n')
            await self.writer.drain()
        except Exception:
            self.writer = None

mixer_client = MixerClient()

class ControlClient:
    def __init__(self):
        self.writer = None

    async def send(self, data):
        if not self.writer or self.writer.transport.is_closing():
            try:
                _, self.writer = await asyncio.open_unix_connection(CONTROL_SOCKET)
            except Exception:
                self.writer = None
                return
        try:
            self.writer.write(json.dumps(data).encode() + b'\n')
            await self.writer.drain()
        except Exception:
            self.writer = None

control_client = ControlClient()

# --- 3. FastAPI 生命周期 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动后台监听任务
    task = asyncio.create_task(socket_listener())
    yield
    # 退出清理
    task.cancel()
    if os.path.exists(WEBAPP_SOCKET):
        os.remove(WEBAPP_SOCKET)

app = FastAPI(lifespan=lifespan)

# --- 4. 路由接口 ---

# 挂载静态文件 (假设 index.html 同级目录)
# 也可以直接返回 FileResponse
@app.get("/")
async def get_index():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

@app.get("/api/config")
async def get_config():
    """读取配置文件"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return JSONResponse(content=json.load(f))
        except:
            pass
    return JSONResponse(content={"target": "disabled", "max_rpm": 90})

def _date_to_epoch(date_str: str, end_of_day: bool = False):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(time.mktime(dt.timetuple()))

@app.get("/api/sessions")
async def list_sessions(from_: str = Query(None, alias="from"), to: str = None, page: int = 1, page_size: int = 20):
    start_ts = None
    end_ts = None
    try:
        if from_:
            start_ts = _date_to_epoch(from_)
        if to:
            end_ts = _date_to_epoch(to, end_of_day=True) + 1
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid date format"})
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))
    total, items = await session_store.list_sessions(start_ts, end_ts, page, page_size)
    return JSONResponse(content={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items
    })

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: int):
    row = await session_store.get_session(session_id)
    if not row:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return JSONResponse(content=row)

@app.get("/api/ftms/status")
async def ftms_status():
    try:
        out = subprocess.check_output(["systemctl", "is-active", "BikeCon-ftms.service"], text=True).strip()
        active = out == "active"
        return JSONResponse(content={"active": active, "state": out})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/ftms/start")
async def ftms_start():
    try:
        subprocess.check_call(["systemctl", "start", "BikeCon-ftms.service"])
        return JSONResponse(content={"ok": True})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/ftms/stop")
async def ftms_stop():
    try:
        subprocess.check_call(["systemctl", "stop", "BikeCon-ftms.service"])
        return JSONResponse(content={"ok": True})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    try:
        await websocket.send_text(json.dumps(session_tracker.session_state_payload()))
    except Exception:
        pass
    try:
        while True:
            # 接收来自前端的指令 (如切换源、虚拟按键)
            data = await websocket.receive_json()
            
            # 转发给 Mixer
            msg_type = data.get('type')
            if msg_type in ['bike_config', 'source', 'btn', 'axis', 'trigger']:
                # 简单的数据转换逻辑保持不变
                if msg_type == 'btn':
                    payload = {
                        "type": "input", 
                        "source": "virtual", 
                        "target": "button", 
                        "id": data.get('id'), 
                        "val": data.get('val')
                    }
                elif msg_type == 'source':
                    payload = {"type": "set_source", "value": data.get('val')}
                else:
                    payload = data
                
                # 异步发送给 Mixer
                await mixer_client.send(payload)
            elif msg_type in ['control', 'set_resistance']:
                if msg_type == 'control':
                    action = data.get('action')
                    if action in ['start', 'stop', 'pause', 'wake']:
                        payload = {"type": action}
                    else:
                        payload = None
                else:
                    payload = {
                        "type": "set_resistance",
                        "level": data.get('level', 10)
                    }
                if payload:
                    await control_client.send(payload)

    except WebSocketDisconnect:
        active_websockets.remove(websocket)
    except Exception:
        active_websockets.remove(websocket)

if __name__ == "__main__":
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except KeyboardInterrupt:
        pass
