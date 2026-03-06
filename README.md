# BikeCon - Bluetooth Bike Controller

BikeCon 是一个将智能自行车转换为游戏手柄的系统，通过BLE连接到自行车并模拟USB游戏手柄输入。

## 快速开始

### 安装
```bash
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

BikeCon 包含以下5个systemd服务，按启动顺序排列：

1. **BikeCon-hardware.service** - USB硬件配置
2. **BikeCon-mixer.service** - 游戏手柄混音器
3. **BikeCon-bike.service** - BLE自行车连接
4. **BikeCon-joycon.service** - Joy-Con输入处理
5. **BikeCon-web.service** - Web界面 (端口8080)

## Web界面

启动后访问：http://localhost:8080

## 日志查看

查看所有服务日志：
```bash
journalctl -u BikeCon-*.service -f
```

查看特定服务日志：
```bash
journalctl -u BikeCon-bike.service -f
```

## 配置

- `config.json` - 应用配置 (目标速度、最大RPM等)
- `identity.json` - 隐私数据 (MAC地址、UUID等)

## 开发

### 依赖安装
```bash
pip install -r requirements.txt
```

### 生成身份文件 (仅开发环境)
```bash
python identity_gen.py
```

## 故障排除

1. 确保已运行 `install.sh` 安装服务
2. 检查BLE适配器是否可用
3. 验证 `identity.json` 文件存在且正确
4. 查看服务日志以获取详细错误信息

## 架构

```
自行车 (BLE) → bike_driver.py → mixer.py → USB游戏手柄
                    ↓
               webapp.py (Web界面)
```

## 许可证

[添加许可证信息]