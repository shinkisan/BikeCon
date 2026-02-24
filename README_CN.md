# Keep C2Lite Controller

[English Version](./README.md)

基于树莓派的室内健身单车控制器，通过蓝牙连接健身单车，支持JoyCon手柄作为输入设备。

## 特性

- **蓝牙连接单车**: 通过蓝牙LE连接ECOTREK健身单车
- **JoyCon支持**: 使用任天堂JoyCon手柄作为输入设备
- **实时网页仪表盘**: 通过网页界面实时查看骑行数据
- **HID输出**: 模拟键盘/鼠标输入，兼容游戏集成
- **Systemd服务**: 完整的systemd集成，支持开机自启和服务管理

## 架构

- `bike_service.py` - 健身单车蓝牙通信
- `joycon_service.py` - JoyCon手柄输入处理
- `mixer.py` - 输入源混合与HID输出
- `webapp.py` + `index.html` - 实时数据网页仪表盘
- `bike_driver.py` - 底层单车协议实现

## 安装

1. 安装依赖:
```bash
pip install fastapi uvicorn
```

2. 如需更改单车MAC地址，在服务文件中配置

3. 安装systemd服务:
```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable c2lite-*
```

4. 启动服务:
```bash
sudo systemctl start c2lite-bike c2lite-joycon c2lite-mixer c2lite-web
```

## 配置

编辑 `config.json` 设置单车类型和最大RPM:
```json
{
  "target": "rt",
  "max_rpm": 100
}
```

## 网页界面

启动web服务后，通过 `http://<树莓派IP>:8000` 访问仪表盘。

## License

GPL v3
