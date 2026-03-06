import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# 配置路径
BASE_DIR = Path("/opt/BikeCon")
CONFIG_FILE = Path("/etc/BikeCon/config.json")

# Socket 路径
WEBAPP_SOCKET = "/var/run/BikeCon/webapp.sock"  # 接收来自 bike_service 的数据
MIXER_SOCKET = "/var/run/BikeCon/mixer.sock"    # 发送指令给 mixer (控制源切换等)

# 确保运行时目录存在
try:
    os.makedirs("/var/run/BikeCon", exist_ok=True)
except PermissionError:
    # 降级方案：使用 /tmp 作为备用
    WEBAPP_SOCKET = "/tmp/BikeCon/webapp.sock"
    MIXER_SOCKET = "/tmp/BikeCon/mixer.sock"
    os.makedirs("/tmp/BikeCon", exist_ok=True)

# --- 全局状态 ---
active_websockets: Set[WebSocket] = set()

# --- 1. 后台监听任务：接收单车数据并广播 ---
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

                # 尝试解析 JSON 确保数据完整性 (可选，为了性能也可以直接转发字符串)
                try:
                    # 广播给所有连接的浏览器
                    if active_websockets:
                        await asyncio.gather(
                            *[ws.send_text(data_str) for ws in active_websockets],
                            return_exceptions=True
                        )
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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
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

    except WebSocketDisconnect:
        active_websockets.remove(websocket)
    except Exception:
        active_websockets.remove(websocket)

if __name__ == "__main__":
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except KeyboardInterrupt:
        pass