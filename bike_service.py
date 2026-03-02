import asyncio
import json
import socket
import os
import logging
import logging.handlers
import shutil
import atexit
import glob
import signal
from pathlib import Path
from bike_driver import BikeClient, BikeData, BikeStatus

# Load bike MAC from config.json (REQUIRED).
# This must be supplied by the user to prevent hardcoding device MAC in the repository.
BIKE_MAC = None
try:
    cfg_path = Path(__file__).parent / "config.json"
    if cfg_path.exists():
        with cfg_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
            mac = cfg.get("bike_mac")
            if isinstance(mac, str) and mac:
                BIKE_MAC = mac
except Exception as e:
    print(f"[BikeService] Failed to load bike_mac from config.json: {e}")

if not BIKE_MAC:
    raise ValueError(
        "[BikeService] CRITICAL: 'bike_mac' not found or empty in config.json. "
        "This field is required and contains your device's Bluetooth MAC address. "
        "Please configure it locally in config.json (add to .gitignore to keep it private)."
    )
SOCKET_PATH = "/tmp/c2lite_mixer.sock"
WEBAPP_SOCKET = "/tmp/c2lite_webapp.sock"
BIKE_ACTIVE_FLAG = "/tmp/c2lite_bike_active"

# --- 日志配置 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
RAM_LOG_PATH = "/tmp/bike_raw_data.log"
PERSISTENT_LOG_DIR = os.path.join(CURRENT_DIR, "logs")
os.makedirs(PERSISTENT_LOG_DIR, exist_ok=True)
MAX_LOG_SIZE = 2 * 1024 * 1024 
BACKUP_COUNT = 1

logger = logging.getLogger("BikeData")
logger.setLevel(logging.INFO)

handler = logging.handlers.RotatingFileHandler(
    RAM_LOG_PATH, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT
)
formatter = logging.Formatter('%(asctime)s.%(msecs)03d | %(message)s', datefmt='%H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)

def save_logs_to_disk():
    print("[Log] 正在将内存日志回写到 SD 卡...")
    try:
        for temp_file in glob.glob(f"{RAM_LOG_PATH}*"):
            if os.path.exists(temp_file):
                filename = os.path.basename(temp_file)
                dest_path = os.path.join(PERSISTENT_LOG_DIR, filename)
                shutil.copy2(temp_file, dest_path)
                print(f"[Log] 已保存: {dest_path}")
    except Exception as e:
        print(f"[Log] 保存失败: {e}")

atexit.register(save_logs_to_disk)

# --- 异步 Unix Socket 客户端 ---
class AsyncUnixClient:
    def __init__(self, path, name):
        self.path = path
        self.name = name
        self.writer = None

    async def ensure_connection(self):
        if self.writer and not self.writer.transport.is_closing():
            return True
        try:
            _, self.writer = await asyncio.open_unix_connection(self.path)
            return True
        except:
            self.writer = None
            return False

    async def send(self, data):
        if await self.ensure_connection():
            try:
                self.writer.write(json.dumps(data).encode() + b'\n')
                await self.writer.drain()
            except:
                self.writer = None

class BikeBridge:
    def __init__(self):
        # 初始化持久连接
        self.mixer = AsyncUnixClient(SOCKET_PATH, "Mixer")
        self.webapp = AsyncUnixClient(WEBAPP_SOCKET, "WebApp")

        self.client = BikeClient(
            BIKE_MAC, 
            data_callback=self.on_data, 
            status_callback=self.on_status
        )

    def send_data(self, payload):
        """异步发送数据到 Mixer 和 WebApp"""
        asyncio.create_task(self.mixer.send(payload))
        asyncio.create_task(self.webapp.send(payload))

    def on_data(self, data: BikeData):
        """蓝牙回调：把全量数据转发给 Mixer 和 WebApp"""
        
        # 1. 组装更丰富的消息体 (供前端/Mixer使用)
        msg = {
            "type": "bike_data", 
            "rpm": data.rpm,
            "power": data.power,
            "duration": data.duration,     # 新增: 运动时长(秒)
            "resistance": data.resistance, # 新增: 阻力档位
            "calories": data.calories,     # 新增: 消耗(kcal)
            "seq": data.status_code        # 新增: 包序号/状态位
        }
        
        # 2. 格式化日志输出
        # 使用更紧凑的格式，Status Code 显示为 16 进制以便调试
        # raw_data 做判空处理
        raw_hex = data.raw_data if data.raw_data else "N/A"
        
        log_msg = (
            f"HEX: {raw_hex:<48} | "  # 预留空间对齐HEX
            f"RPM: {data.rpm:<3} | "
            f"PWR: {data.power:<3} | "
            f"RES: {data.resistance:<2} | "
            f"TIME: {data.duration:<4} | "
            f"KCAL: {data.calories:<3} | "
            f"SEQ: {data.status_code:02X}" # 显示为 16 进制 (如 C3, B9)
        )
        
        logger.info(log_msg)
        self.send_data(msg)
    
    def on_status(self, old_status, new_status):
        is_active = (new_status == BikeStatus.ACTIVE)
        print(f"\n[Bridge] 状态变更: {old_status.name} -> {new_status.name}")

        try:
            if is_active:
                with open(BIKE_ACTIVE_FLAG, 'w') as f: f.write("1")
            else:
                if os.path.exists(BIKE_ACTIVE_FLAG):
                    os.remove(BIKE_ACTIVE_FLAG)
        except Exception as e:
            print(f"Flag Error: {e}")
        
        # 发送状态消息
        self.send_data({
            "type": "bike_status", 
            "active": is_active
        })

    async def run(self):
        print(f"[Bridge] 启动蓝牙服务 ({BIKE_MAC})...")
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _signal_handler():
            print("[Bridge] 收到终止信号，开始清理...")
            stop_event.set()

        # 注册 Unix 信号处理（也适用于 Ctrl+C）
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows 的事件循环不支持 add_signal_handler
                pass

        while True:
            try:
                success = await self.client.connect()

                if success:
                    print(f"\n[Bridge] 驱动启动成功...")
                    while self.client.running and not stop_event.is_set():
                        await asyncio.sleep(1)
                    print(f"\n[Bridge] 驱动停止运行 (Running=False or signal)")
                else:
                    print(f"[Bridge] 启动失败，无法找到设备或连接被拒绝")

            except Exception as e:
                print(f"[Bridge] 运行时严重错误: {e}")

            if stop_event.is_set():
                break

            print("[Bridge] 5秒后尝试重启服务...")
            await asyncio.sleep(5)

        # 在退出前断开蓝牙
        try:
            await self.client.disconnect(is_retry=False)
        except Exception as e:
            print(f"[Bridge] 退出断开时发生错误: {e}")
        print("[Bridge] 已安全退出")


if __name__ == "__main__":
    bridge = BikeBridge()
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass