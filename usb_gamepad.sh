#!/bin/bash
modprobe libcomposite
mount -t configfs none /sys/kernel/config 2>/dev/null

# --- 清理旧配置 ---
func_cleanup() {
    GADGET_DIR="/sys/kernel/config/usb_gadget/g1"
    if [ -d "$GADGET_DIR" ]; then
        echo "" > "$GADGET_DIR/UDC" 2>/dev/null
        rm "$GADGET_DIR/configs/c.1/hid.usb0" 2>/dev/null
        rmdir "$GADGET_DIR/configs/c.1/strings/0x409" 2>/dev/null
        rmdir "$GADGET_DIR/configs/c.1" 2>/dev/null
        rmdir "$GADGET_DIR/functions/hid.usb0" 2>/dev/null
        rmdir "$GADGET_DIR/strings/0x409" 2>/dev/null
        rmdir "$GADGET_DIR" 2>/dev/null
    fi
}
func_cleanup

# --- 创建新设备 ---
mkdir -p /sys/kernel/config/usb_gadget/g1
cd /sys/kernel/config/usb_gadget/g1 || exit 1

# 身份信息
echo 0x1d6b > idVendor
echo 0xA005 > idProduct
echo 0x0110 > bcdUSB
echo 0x0100 > bcdDevice

# 硬件描述字符串
mkdir -p strings/0x409
echo "fedcba9876543210" > strings/0x409/serialnumber
echo "C2Lite Lab" > strings/0x409/manufacturer
echo "C2Lite V2 Controller" > strings/0x409/product

# 功能定义
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 9 > functions/hid.usb0/report_length

# 结构：ReportID(1) | X(1B) | Y(1B) | Z(1B) | Rz(1B) | Rx(1B) | Ry(1B) | Buttons(2B)
echo -ne \\x05\\x01\\x09\\x05\\xa1\\x01\\x85\\x01\\x05\\x01\\x09\\x30\\x09\\x31\\x09\\x32\\x09\\x35\\x15\\x00\\x26\\xff\\x00\\x75\\x08\\x95\\x04\\x81\\x02\\x05\\x01\\x09\\x33\\x09\\x34\\x15\\x00\\x26\\xff\\x00\\x75\\x08\\x95\\x02\\x81\\x02\\x05\\x09\\x19\\x01\\x29\\x10\\x15\\x00\\x25\\x01\\x75\\x01\\x95\\x10\\x81\\x02\\xc0 > functions/hid.usb0/report_desc

# 绑定与启动
mkdir -p configs/c.1/strings/0x409
echo "Config 1" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower
ln -s functions/hid.usb0 configs/c.1/
ls /sys/class/udc > UDC

echo "USB Gadget 已启动"