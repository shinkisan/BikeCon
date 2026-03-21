# BikeCon

BikeCon 是一个将 **Keep 动感单车** 协议解析并映射为 **虚拟游戏手柄** 的系统。它允许你通过 Linux 设备（推荐树莓派 Zero 2W）作为中继，在电脑上使用单车作为输入设备 。它同时附带一个FTMS兼容层，提供给想要游玩GTBIKEV，或者Zwift的玩家。

## 硬件需求
- **Keep 动感单车**: 目前已支持 Keep C2 Lite（固件版本1.0.1）。
- **中继电脑**: 一个运行 Linux 的小型电脑（需自带蓝牙并支持 USB Gadget 模式，如树莓派 Zero 2W） 。接下来用树莓派泛指该类型设备。
- **Joy-Con（可选）**: 用于组合按键输入。如果没有可以使用程序配套的网页端虚拟手柄。

---
## 克隆代码到树莓派上
```bash
# 克隆项目
git clone https://github.com/shinkisan/BikeCon.git

# 进入项目目录
cd BikeCon
```

## 第一步：准备鉴权信息 (必须)

在安装项目之前，你必须从官方 App 的通信中提取鉴权所需信息。

**⚠️重要：你的单车（包括以后使用该程序时）必须处于断网状态，否则所有数据都会走wifi网络**

### 1.1 从安卓设备提取 HCI 日志
1. **开启开发者模式**: 在 “关于手机” 页面，查找 “版本号” 或 “软件版本号”，持续点击直到屏幕提示 “已进入开发者模式”。
2. **启用 HCI 收集**: 进入“开发者选项”，开启 **“启用蓝牙 HCI 监听日志”**。
3. **产生通信数据**:
   - 重启手机蓝牙。
   - 打开 **Keep App**，连接你的单车并骑行几分钟。
   - 结束运动，关闭 Keep App。
4. **导出日志**: 
   - 找到手机存储中的日志文件（通常在 `/data/misc/bluetooth/logs/btsnoop_hci.log` ；或通过 `adb bugreport bugreport.zip`导出，解压后一般在`FS/data/misc/bluetooth/logs/btsnoop_hci.log`）。
   - 将该文件发送至你的树莓派。

### 1.2 生成配置文件
项目提供了一个自动提取工具 `identity_gen.py`，它会解析二进制日志并生成 `identity.json` 。

**环境准备**:
```bash
# 安装抓包解析引擎
sudo apt install tshark -y
# 安装 Python 依赖
pip install pyshark
# 将你的日志文件（如 btsnoop_hci.log）作为参数
python3 identity_gen.py btsnoop_hci.log
```
执行完成后，本地会生成一个 identity.json 文件。请检查数据准确性。

## 第二步：安装与启动

### 安装
```bash
chmod +x install.sh
sudo ./install.sh
```

### 启动服务
```bash
chmod +x start.sh
sudo ./start.sh
```

### 停止服务
```bash
chmod +x stop.sh
sudo ./stop.sh
```

### 卸载
```bash
chmod +x uninstall.sh
sudo ./uninstall.sh
```

## 服务说明

BikeCon 包含以下 6 个 systemd 服务，按启动顺序排列：

1. **BikeCon-hardware.service** - 配置 USB Gadget，模拟 HID 手柄
2. **BikeCon-mixer.service** - 混合单车数据与手柄按键
3. **BikeCon-bike.service** - BLE自行车连接
4. **BikeCon-joycon.service** - Joy-Con输入处理
5. **BikeCon-web.service** - Web界面 (端口8000)
6. **BikeCon-ftms.service** - FTMS 兼容层（对外提供 FTMS BLE 服务）

## FTMS 兼容层（可选）

项目内置了一个 FTMS 兼容层，可将单车数据通过标准 FTMS 服务对外广播，用于兼容部分第三方应用（例如 **GTBIKEV**）。

- 默认状态：`config.json` 中 `ftms_layer_enabled` 默认是 `false`（关闭）
- 启用方式 1（推荐）：打开 Web 设置页，将“FTMS 服务”切换为开启
- 启用方式 2：手动编辑 `/etc/BikeCon/config.json`，将 `ftms_layer_enabled` 改为 `true`

FTMS 服务进程会轮询配置并自动生效，通常无需手动重启服务。

## Web界面

启动后访问：http://<树莓派IP>:8000

## 日志查看
跟踪单车数据包（运行时）：
```bash
tail -f /dev/shm/BikeCon/bike_raw_data.log
```

跟踪单车数据包（持久化保存）：
```bash
tail -f /var/log/BikeCon/bike_raw_data.log
```

查看所有服务日志：
```bash
journalctl -u BikeCon-*.service -f
```

查看特定服务日志：
```bash
journalctl -u BikeCon-bike.service -f
```

## 配置

- `config.json` - 应用配置
- `identity.json` - 鉴权数据

## 问题反馈

项目未经充分测试，如果遇到问题或请求其它型号支持，请带上/var/log/BikeCon/bike_raw_data.log提issue

## 架构

```
自行车 (BLE) → bike_driver.py → bike_service.py → mixer.py → USB游戏手柄
                                 ↓             ↑
                           webapp.py（管理界面）  webapp.py（虚拟手柄）/joycon_service.py
                                 ↓
                           ftms_server.py（FTMS兼容层） → 第三方App（如 GTBIKEV）
```

## 许可与声明

本项目采用 GNU GPL v3 协议开源

本项目仅用于技术研究与个人学习，不保证对所有硬件和固件版本的兼容性。因使用本项目导致的设备问题或 Keep 账号异常，作者概不负责。

本项目大量使用AI，代码风格杂乱，中英双语日志和备注乱飞，有时间会慢慢打磨😝。

## 特别感谢

FTMS 兼容层功能的实现参考了以下项目的代码与思路，特此感谢：

- https://github.com/happyderekl/Bike-FTMS-Bridge


