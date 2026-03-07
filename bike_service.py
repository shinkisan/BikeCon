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

IDENTITY_PATH = Path("/etc/BikeCon/identity.json")

# Load bike MAC from identity.json (REQUIRED).
BIKE_MAC = None
try:
    if IDENTITY_PATH.exists():
        with IDENTITY_PATH.open(encoding="utf-8") as f:
            cfg = json.load(f)
            mac = cfg.get("bike_mac")
            if isinstance(mac, str) and mac:
                BIKE_MAC = mac
except Exception as e:
    print(f"[BikeService] Failed to load bike_mac from identity.json: {e}")

if not BIKE_MAC:
    raise ValueError(
        "[BikeService] CRITICAL: 'bike_mac' not found or empty in identity.json. "
        "This field is required and contains your device's Bluetooth MAC address. "
        "Please configure it locally in identity.json (add to .gitignore to keep it private)."
    )
SOCKET_PATH = "/var/run/BikeCon/mixer.sock"
WEBAPP_SOCKET = "/var/run/BikeCon/webapp.sock"
BIKE_ACTIVE_FLAG = "/var/run/BikeCon/bike_active"

# 确保运行时目录存在
try:
    os.makedirs("/var/run/BikeCon", exist_ok=True)
except PermissionError:
    # 降级方案：使用 /tmp 作为备用
    SOCKET_PATH = "/tmp/BikeCon/mixer.sock"
    WEBAPP_SOCKET = "/tmp/BikeCon/webapp.sock"
    BIKE_ACTIVE_FLAG = "/tmp/BikeCon/bike_active"
    os.makedirs("/tmp/BikeCon", exist_ok=True)

# --- 日志配置 ---
RAM_DISK_DIR = "/dev/shm/BikeCon" 
os.makedirs(RAM_DISK_DIR, exist_ok=True)
# 内存中的临时日志路径
RAM_LOG_PATH = os.path.join(RAM_DISK_DIR, "bike_raw_data.log")
# 磁盘上的持久化目录
PERSISTENT_LOG_DIR = "/var/log/BikeCon"
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
    print("[Log] 正在将内存日志写入磁盘...")
    try:
        # 获取内存中所有的日志文件（包括旋转产生的 .1, .2 等）
        for temp_file in glob.glob(f"{RAM_LOG_PATH}*"):
            if os.path.exists(temp_file):
                filename = os.path.basename(temp_file)
                dest_path = os.path.join(PERSISTENT_LOG_DIR, filename)
                
                # 核心检查：如果源路径和目标路径相同，则跳过
                if os.path.abspath(temp_file) == os.path.abspath(dest_path):
                    continue
                    
                shutil.copy2(temp_file, dest_path)
                print(f"[Log] 已同步到磁盘: {dest_path}")
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
        """蓝牙回调：转发优化后的全量数据"""
        
        # 1. 组装全量数据消息体 (新增 speed 和 distance)
        msg = {
            "type": "bike_data", 
            "rpm": data.rpm,
            "power": data.power,
            "speed": data.speed,       # 新增: 实时速度 (km/h)
            "distance": data.distance, # 新增: 累计距离 (m)
            "duration": data.duration,
            "resistance": data.resistance,
            "calories": data.calories,
            "status": data.status_code # 建议将字段名改为 status，因为它代表机器状态
        }
        
        # 2. 优化日志格式
        # 增加 SPD(速度) 和 DST(距离)，并将 SEQ 改为更准确的 STA(状态)
        raw_hex = data.raw_data if data.raw_data else "N/A"
        
        log_msg = (
            f"HEX: {raw_hex:<48} | "
            f"RPM: {data.rpm:<3} | "
            f"PWR: {data.power:<3} | "
            f"SPD: {data.speed:<4.1f} | "  # 新增速度显示
            f"DST: {data.distance:<5} | "  # 新增距离显示
            f"RES: {data.resistance:<2} | "
            f"TIME: {data.duration:<4} | "
            f"KCAL: {data.calories:<3.1f} | "
            f"STA: {data.status_code:02X}"   # 原 SEQ 现改为 STA (Status)
        )
        
        logger.info(log_msg)
        self.send_data(msg)
    
    def on_status(self, old_status, new_status):
        # 严格判断：只有 3 (ACTIVE) 才算真正激活
        is_active = (new_status == BikeStatus.ACTIVE)
        print(f"\n[Bridge] 状态变更: {old_status.name} -> {new_status.name}")

        # 控制系统层面的活跃标志文件
        try:
            if is_active:
                with open(BIKE_ACTIVE_FLAG, 'w') as f: f.write("1")
            else:
                if os.path.exists(BIKE_ACTIVE_FLAG):
                    os.remove(BIKE_ACTIVE_FLAG)
        except Exception as e:
            print(f"Flag Error: {e}")
        
        # 给前端发送更细致的消息，前端可以据此切换 UI 状态（如显示“已暂停”、“321倒计时”等）
        self.send_data({
            "type": "bike_status", 
            "active": is_active,
            "status_name": new_status.name, # 发送 "READY", "IDLE", "ACTIVE", "PAUSED"
            "status_code": new_status.value # 发送 1, 2, 3, 4
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

        try:
            # 【修改点 1】只需要调用一次 start()。
            # 驱动内部的看门狗会自动接管：尝试连接、断线重连等所有脏活累活。
            await self.client.start()
            print(f"\n[Bridge] 驱动已启动...")

            # 【修改点 2】抛弃原先的 while True 和 sleep 循环。
            # 直接挂起主协程，直到收到终止信号 (Ctrl+C 或 systemctl stop)
            await stop_event.wait()

        except Exception as e:
            print(f"[Bridge] 运行时严重错误: {e}")
            
        finally:
            # 【修改点 3】在退出前调用统一的 stop() 接口，安全清理所有任务和蓝牙连接
            print("[Bridge] 正在停止蓝牙驱动...")
            await self.client.stop()
            print("[Bridge] 已安全退出")

if __name__ == "__main__":
    bridge = BikeBridge()
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass