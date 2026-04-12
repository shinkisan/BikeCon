# BikeCon

[English README](./README_EN.md)

BikeCon 是一个将动感单车数据映射为 **虚拟游戏手柄** 的系统。它允许你通过 Linux 设备（推荐树莓派 Zero 2W）作为中继，在电脑上使用单车作为输入设备。

目前已支持两类输入协议：

- **部分 Keep 动感单车私有协议**
- **FTMS 通用协议设备**

此外，项目仍保留一个可选的 **FTMS 兼容层服务**，用于将KEEP单车的数据再广播为 FTMS，供第三方应用（如 GTBIKEV / Zwift）连接。

## 硬件需求
- **骑行设备（满足其一）**:
  - Keep 动感单车（目前已支持 Keep C2 Lite，固件版本 1.0.1）
  - 支持 FTMS 的通用 BLE 动感单车
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

## 第一步：准备设备身份信息

根据你使用的单车类型，流程不同：

- **Keep 模式**：需要从 Keep App 通信中提取鉴权信息。
- **FTMS 模式**：不需要 Keep 抓包，直接扫描并选择目标 FTMS 设备。

---
### Keep 模式：准备鉴权信息 (必须)

在安装项目之前，你必须从官方 App 的通信中提取鉴权所需信息。

**重要：你的单车（包括以后使用该程序时）必须处于断网状态，否则所有数据都会走wifi网络**

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

### Keep 模式：生成配置文件
项目提供了自动工具 `identity_gen.py`，会生成 `identity.json`，并自动更新 `config.json` 里的 `bike_type`。

**Keep 模式（有日志参数）**:
```bash
# 安装抓包解析依赖
sudo apt install tshark -y
pip install pyshark
python3 identity_gen.py btsnoop_hci.log
```

### FTMS 模式：生成配置文件（无需抓包）

**通用 FTMS 模式（无参数）**:
```bash
python3 identity_gen.py
```
运行后会提示你按 `Enter` 进入 BLE 扫描（`Esc` 退出），然后选择设备生成 `identity.json`。

## 第二步：安装与启动

### 安装
```bash
chmod +x install.sh
sudo ./install.sh
```

### 启动服务
```bash
sudo ./start.sh
```

### 停止服务
```bash
sudo ./stop.sh
```

### 卸载
```bash
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

项目内置了一个 FTMS 兼容层，可将KEEP单车数据通过标准 FTMS 服务对外广播，用于兼容部分第三方应用（例如 **GTBIKEV**）。

- 默认状态：`config.json` 中 `ftms_layer_enabled` 默认是 `false`（关闭）
- 启用方式 1（推荐）：打开 Web 设置页，将“FTMS 服务”切换为开启
- 启用方式 2：手动编辑 `/etc/BikeCon/config.json`，将 `ftms_layer_enabled` 改为 `true`
- 强制关闭规则：当 `config.json` 中 `bike_type` 为 `ftms` 时，FTMS 兼容层会被强制关闭（Web 按钮会显示不可用）

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

项目未经充分测试，如果遇到问题或请求其它型号支持，请带上相关日志提issue

## 开发

#### 架构

```
自行车 (BLE) → bike_driver_xxxx.py → bike_service.py → mixer.py → USB游戏手柄
                              ↓             ↑
                        webapp.py   webapp.py（虚拟手柄）/joycon_service.py
                              ↓
                        ftms_server.py（FTMS兼容层） → 第三方App（如 GTBIKEV）
```

### 联调脚本
如果你想要测试，这两个脚本可能对你有所帮助：

### 1) `dev/fake_ftms_server.py`

用途：在没有真实 FTMS 设备时，模拟一台可被 BikeCon 连接的 FTMS 动感单车。

常用命令：

```bash
python3 dev/fake_ftms_server.py
python3 dev/fake_ftms_server.py --name BikeCon-Fake-FTMS --hz 5 --web-port 8080
python3 dev/fake_ftms_server.py --start-active
```

启动后：

- 网页管理默认地址：`http://<设备IP>:8080`
- 在 BikeCon 使用 FTMS 模式，并将 `identity.json` 的 `bike_mac` 指向该模拟设备（可通过扫描获得）

### 2) `dev/fake_ftms_client.py`

用途：作为 FTMS 客户端连接到 FTMS 服务（可连接 BikeCon 的 FTMS 兼容层，或 fake_ftms_server）。

常用命令：

```bash
# 按名称扫描并连接（默认目标 BikeCon-FTMS）
python3 dev/fake_ftms_client.py

# 指定设备名或 MAC
python3 dev/fake_ftms_client.py BikeCon-Fake-FTMS
python3 dev/fake_ftms_client.py AA:BB:CC:DD:EE:FF

# 交互模式
python3 dev/fake_ftms_client.py -i

# 无 Web UI（仅控制台）
python3 dev/fake_ftms_client.py --no-web
```

说明：

- `fake_ftms_client.py` 默认 Web 端口是 `8080`（`--port` 可改）
- `fake_ftms_server.py` 的 fake_ftms_ui 默认也是 `8080`
- 如果两个脚本在同一台机器同时运行，请至少修改一个端口，避免冲突

## 许可与声明

本项目采用 GNU GPL v3 协议开源

本项目仅用于技术研究与个人学习，不保证对所有硬件和固件版本的兼容性。因使用本项目导致的设备问题或 Keep 账号异常，作者概不负责。

本项目大量使用AI，代码风格杂乱，中英双语日志和备注乱飞，有时间会慢慢打磨😝。

## 特别感谢

FTMS 兼容层功能的实现参考了以下项目的代码与思路，特此感谢：

- https://github.com/happyderekl/Bike-FTMS-Bridge


