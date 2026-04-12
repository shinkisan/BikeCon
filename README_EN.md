# BikeCon

[中文说明](./README.md)

BikeCon maps indoor bike data to a **virtual game controller**. It lets you use a Linux device (Raspberry Pi Zero 2W recommended) as a bridge so your bike can act as an input device on a PC.

BikeCon currently supports two input protocol types:

- **Private protocol of some Keep bikes**
- **Standard FTMS-compatible devices**

In addition, BikeCon keeps an optional **FTMS compatibility layer service** that rebroadcasts Keep bike data as FTMS for third-party apps (for example, GTBIKEV / Zwift).

## Hardware Requirements
- **Cycling device (one of the following):**
  - Keep bike (currently tested with Keep C2 Lite, firmware 1.0.1)
  - Generic BLE indoor bike with FTMS support
- **Bridge device:** A small Linux computer with Bluetooth and USB Gadget support (e.g. Raspberry Pi Zero 2W). In this document, this type of device is referred to as “Raspberry Pi”.
- **Joy-Con (optional):** For combined button input. If unavailable, use the built-in web virtual controller.

---
## Clone to Raspberry Pi
```bash
# Clone repo
git clone https://github.com/shinkisan/BikeCon.git

# Enter project directory
cd BikeCon
```

## Step 1: Prepare Device Identity

The flow depends on your bike type:

- **Keep mode:** You need to extract auth info from Keep App communication.
- **FTMS mode:** No Keep packet capture required; just scan and select the target FTMS device.

---
### Keep Mode: Prepare Auth Info (Required)

Before installation, you must extract authentication information from official app traffic.

**Important: your bike must stay offline (including future usage with this project), otherwise data may go through Wi-Fi instead of BLE.**

### 1.1 Extract HCI logs from Android
1. **Enable Developer Options:** In “About phone”, tap “Build number” / “Software version” repeatedly until Developer Mode is enabled.
2. **Enable HCI logging:** In Developer Options, enable **Bluetooth HCI snoop log**.
3. **Generate traffic:**
   - Restart Bluetooth on the phone.
   - Open **Keep App**, connect your bike, and ride for a few minutes.
   - End workout and close Keep App.
4. **Export logs:**
   - Locate the log file (usually `/data/misc/bluetooth/logs/btsnoop_hci.log`; or export via `adb bugreport bugreport.zip`, then it is typically under `FS/data/misc/bluetooth/logs/btsnoop_hci.log`).
   - Copy the file to your Raspberry Pi.

### Keep Mode: Generate config
BikeCon provides `identity_gen.py`, which generates `identity.json` and automatically updates `bike_type` in `config.json`.

**Keep mode (with log file argument):**
```bash
# Install packet parsing dependencies
sudo apt install tshark -y
pip install pyshark
python3 identity_gen.py btsnoop_hci.log
```

### FTMS Mode: Generate config (no packet capture)

**Generic FTMS mode (no arguments):**
```bash
python3 identity_gen.py
```
After launch, press `Enter` to start BLE scan (`Esc` to exit), then choose a device to generate `identity.json`.

## Step 2: Install and Run

### Install
```bash
chmod +x install.sh
sudo ./install.sh
```

### Start services
```bash
sudo ./start.sh
```

### Stop services
```bash
sudo ./stop.sh
```

### Uninstall
```bash
sudo ./uninstall.sh
```

## Services

BikeCon includes these 6 systemd services (startup order):

1. **BikeCon-hardware.service** - Configure USB Gadget and emulate HID gamepad
2. **BikeCon-mixer.service** - Mix bike data and controller button input
3. **BikeCon-bike.service** - BLE bike connection
4. **BikeCon-joycon.service** - Joy-Con input handling
5. **BikeCon-web.service** - Web UI (port 8000)
6. **BikeCon-ftms.service** - FTMS compatibility layer (exposes FTMS BLE service externally)

## FTMS Compatibility Layer (Optional)

BikeCon includes an FTMS compatibility layer that rebroadcasts Keep bike data through standard FTMS service for some third-party apps (e.g. **GTBIKEV**).

- Default: `ftms_layer_enabled` in `config.json` is `false` (disabled)
- Enable method 1 (recommended): turn on “FTMS Service” in Web settings
- Enable method 2: edit `/etc/BikeCon/config.json` and set `ftms_layer_enabled` to `true`
- Forced-off rule: when `bike_type` in `config.json` is `ftms`, FTMS compatibility layer is forcibly disabled (Web button will appear unavailable)

## Web UI

After startup, open: `http://<raspberry-pi-ip>:8000`

## Logs
View bike packet log (runtime):
```bash
tail -f /dev/shm/BikeCon/bike_raw_data.log
```

View bike packet log (persistent):
```bash
tail -f /var/log/BikeCon/bike_raw_data.log
```

View all service logs:
```bash
journalctl -u BikeCon-*.service -f
```

View specific service log:
```bash
journalctl -u BikeCon-bike.service -f
```

## Configuration

- `config.json` - App configuration
- `identity.json` - Authentication data

## Issue Reporting

This project is not fully tested yet. If you hit issues or request support for other models, please open an issue with related logs.

## Development

#### Architecture

```
Bike (BLE) -> bike_driver_xxxx.py -> bike_service.py -> mixer.py -> USB gamepad
                         ↓                  ↑
                      webapp.py     webapp.py (virtual gamepad) / joycon_service.py
                         ↓
                    ftms_server.py (FTMS compatibility layer) -> Third-party apps (e.g. GTBIKEV)
```

### Integration Test Scripts
If you want to test locally, these two scripts may help:

### 1) `dev/fake_ftms_server.py`

Purpose: simulate an FTMS indoor bike that BikeCon can connect to when you do not have real FTMS hardware.

Common commands:

```bash
python3 dev/fake_ftms_server.py
python3 dev/fake_ftms_server.py --name BikeCon-Fake-FTMS --hz 5 --web-port 8080
python3 dev/fake_ftms_server.py --start-active
```

After startup:

- Default web management URL: `http://<device-ip>:8080`
- In BikeCon FTMS mode, point `identity.json` `bike_mac` to this simulated device (can be discovered by scan)

### 2) `dev/fake_ftms_client.py`

Purpose: connect as an FTMS client to an FTMS service (BikeCon FTMS layer or `fake_ftms_server`).

Common commands:

```bash
# Scan and connect by name (default target: BikeCon-FTMS)
python3 dev/fake_ftms_client.py

# Connect by device name or MAC
python3 dev/fake_ftms_client.py BikeCon-Fake-FTMS
python3 dev/fake_ftms_client.py AA:BB:CC:DD:EE:FF

# Interactive mode
python3 dev/fake_ftms_client.py -i

# Console-only mode (no web UI)
python3 dev/fake_ftms_client.py --no-web
```

Notes:

- `fake_ftms_client.py` default web port is `8080` (change with `--port`)
- `fake_ftms_server.py` web UI default is also `8080`
- If both run on the same machine, change at least one port to avoid conflicts

## License and Disclaimer

This project is licensed under GNU GPL v3.

This project is for technical research and personal learning only. Compatibility across all hardware and firmware versions is not guaranteed. The author is not responsible for device issues or Keep account issues caused by using this project.

This project heavily uses AI; code style is still mixed and being improved over time.

## Special Thanks

FTMS compatibility layer implementation references code and ideas from:

- https://github.com/happyderekl/Bike-FTMS-Bridge
