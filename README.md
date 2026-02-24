# Keep C2Lite Controller

[中文版](./README_CN.md)

A Raspberry Pi-based indoor cycling controller that connects to fitness bikes via Bluetooth and uses JoyCon controllers as input devices.

## Features

- **Bluetooth Bike Connection**: Connect to ECOTREK fitness bikes via Bluetooth LE
- **JoyCon Support**: Use Nintendo JoyCon controllers as input devices
- **Real-time Web Dashboard**: Monitor ride data in real-time through a web interface
- **HID Output**: Emulate keyboard/mouse input for game integration
- **Systemd Services**: Full systemd integration for automatic startup and management

## Architecture

- `bike_service.py` - Bluetooth communication with fitness bike
- `joycon_service.py` - JoyCon controller input handling
- `mixer.py` - Input source mixing and HID output
- `webapp.py` + `index.html` - Web dashboard for real-time data
- `bike_driver.py` - Low-level bike protocol implementation

## Setup

1. Install dependencies:
```bash
pip install fastapi uvicorn
```

2. Configure bike MAC address in service files if needed

3. Install systemd services:
```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable c2lite-*
```

4. Start services:
```bash
sudo systemctl start c2lite-bike c2lite-joycon c2lite-mixer c2lite-web
```

## Configuration

Edit `config.json` to set bike target and max RPM:
```json
{
  "target": "rt",
  "max_rpm": 100
}
```

## Web Interface

Access the dashboard at `http://<raspberry-pi-ip>:8000` after starting the web service.

## License

GPL v3
