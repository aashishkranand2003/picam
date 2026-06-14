#!/bin/bash
set -Eeuo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run with sudo: sudo bash install.sh${NC}"
    exit 1
fi

INSTALL_USER=${SUDO_USER:-pi}
INSTALL_ENTRY=$(getent passwd "$INSTALL_USER" || true)
INSTALL_HOME=$(printf '%s' "$INSTALL_ENTRY" | cut -d: -f6)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "$INSTALL_HOME" ]; then
    echo -e "${RED}Could not find a home directory for user '$INSTALL_USER'.${NC}"
    exit 1
fi

INSTALL_GROUP=$(id -gn "$INSTALL_USER")

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  OptoCam Zero — 3.5\" ILI9486 Edition  ${NC}"
echo -e "${GREEN}========================================${NC}"
echo "User: $INSTALL_USER"
echo "Home: $INSTALL_HOME"
echo ""

echo -e "${YELLOW}[1/8] Installing system packages...${NC}"
apt-get update -q
apt-get install -y --no-install-recommends \
    hostapd dnsmasq iw iproute2 \
    python3-flask python3-numpy python3-pil python3-picamera2 \
    python3-rpi.gpio python3-spidev

echo -e "${YELLOW}[2/8] Preparing install paths...${NC}"
install -d -m 0755 -o "$INSTALL_USER" -g "$INSTALL_GROUP" "$INSTALL_HOME/photos"
for group in gpio spi i2c video; do
    if getent group "$group" >/dev/null; then
        usermod -aG "$group" "$INSTALL_USER"
    fi
done

echo -e "${YELLOW}[3/8] Copying scripts and assets...${NC}"
install -m 0755 -o "$INSTALL_USER" -g "$INSTALL_GROUP" "$SCRIPT_DIR/scripts/optocamzero.py"    "$INSTALL_HOME/optocamzero.py"
install -m 0755 -o "$INSTALL_USER" -g "$INSTALL_GROUP" "$SCRIPT_DIR/scripts/gallery_server.py" "$INSTALL_HOME/gallery_server.py"
install -m 0644 -o "$INSTALL_USER" -g "$INSTALL_GROUP" "$SCRIPT_DIR/assets/cmunvt.ttf"         "$INSTALL_HOME/cmunvt.ttf"
install -m 0644 -o "$INSTALL_USER" -g "$INSTALL_GROUP" "$SCRIPT_DIR/assets/optocamlogo.svg"    "$INSTALL_HOME/optocamlogo.svg"
install -m 0644 -o "$INSTALL_USER" -g "$INSTALL_GROUP" "$SCRIPT_DIR/assets/splash.raw"         "$INSTALL_HOME/splash.raw"

sed -i "s|/home/dkumkum|$INSTALL_HOME|g" "$INSTALL_HOME/optocamzero.py"
sed -i "s|/home/dkumkum|$INSTALL_HOME|g" "$INSTALL_HOME/gallery_server.py"

echo -e "${YELLOW}[4/8] Installing systemd services...${NC}"
for svc in camera-auto optocam-hotspot optocam-gallery uap0; do
    install -m 0644 "$SCRIPT_DIR/services/$svc.service" "/etc/systemd/system/$svc.service"
    sed -i "s|/home/dkumkum|$INSTALL_HOME|g" "/etc/systemd/system/$svc.service"
    sed -i "s|dkumkum|$INSTALL_USER|g"       "/etc/systemd/system/$svc.service"
done

echo -e "${YELLOW}[5/8] Configuring hotspot...${NC}"
cp "$SCRIPT_DIR/services/hostapd.conf"         "/etc/hostapd/hostapd.conf"
cp "$SCRIPT_DIR/services/dnsmasq-optocam.conf" "/etc/dnsmasq.d/optocam.conf"

systemctl unmask hostapd
sed -i 's|#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/optocam-unmanaged.conf << 'EOF'
[keyfile]
unmanaged-devices=interface-name:uap0
EOF

echo -e "${YELLOW}[6/8] Configuring /boot/firmware/config.txt...${NC}"
BOOT_DIR=/boot/firmware
if [ ! -f "$BOOT_DIR/config.txt" ]; then
    BOOT_DIR=/boot
fi

CONFIG="$BOOT_DIR/config.txt"
CMDLINE="$BOOT_DIR/cmdline.txt"

if [ ! -f "$CONFIG" ] || [ ! -f "$CMDLINE" ]; then
    echo -e "${RED}Could not find Raspberry Pi boot files in /boot/firmware or /boot.${NC}"
    exit 1
fi

last_section=$(grep -E '^\[[^]]+\]' "$CONFIG" | tail -n 1 || true)
if [ "$last_section" != "[all]" ]; then
    echo "" >> "$CONFIG"
    echo "[all]" >> "$CONFIG"
fi

set_config() {
    local key="$1"
    local value="$2"
    sed -i -E "/^[#[:space:]]*${key}=.*/d" "$CONFIG"
    echo "${key}=${value}" >> "$CONFIG"
}

set_dtparam() {
    local param="$1"
    local value="$2"
    sed -i -E "/^dtparam=${param}=.*/d" "$CONFIG"
    echo "dtparam=${param}=${value}" >> "$CONFIG"
}

add_if_missing() {
    grep -qxF "$1" "$CONFIG" || echo "$1" >> "$CONFIG"
}

sed -i -E '/^dtoverlay=(vc4-kms-v3d|vc4-fkms-v3d|st7789|spi1-3cs).*/d' "$CONFIG"

set_config "camera_auto_detect" "0"
set_config "display_auto_detect" "0"
set_config "enable_uart" "0"
set_dtparam "audio" "off"
set_dtparam "spi" "on"
set_dtparam "i2c_arm" "off"
add_if_missing "dtoverlay=imx708"
set_config "arm_boost" "1"
set_config "arm_freq" "1200"
set_config "over_voltage" "2"
set_config "initial_turbo" "30"
set_config "boot_delay" "0"
set_config "disable_splash" "1"
set_config "auto_initramfs" "0"
add_if_missing "dtoverlay=disable-bt"
set_config "disable_fw_kms_setup" "1"
set_config "disable_overscan" "1"
set_config "hdmi_ignore_hotplug" "1"
set_config "max_framebuffers" "0"

add_if_missing "# OptoCam SPI display setup: ILI9486 userspace driver on SPI0 CE0"
add_if_missing "gpio=24=op,dh"
add_if_missing "gpio=25=op,dh"
add_if_missing "gpio=27=op,dh"

install -d -m 0755 /etc/modules-load.d
cat > /etc/modules-load.d/optocam-spi.conf << 'EOF'
spi_bcm2835
spidev
EOF

append_cmdline_once() {
    case " $(cat "$CMDLINE") " in
        *" $1 "*) ;;
        *) sed -i "s/$/ $1/" "$CMDLINE" ;;
    esac
}

set_cmdline_kv() {
    local key="$1"
    local value="$2"
    local escaped_key
    escaped_key="${key//./\\.}"
    if grep -qE "(^| )${escaped_key}=" "$CMDLINE"; then
        sed -i -E "s/(^| )${escaped_key}=[^ ]*/ ${key}=${value}/" "$CMDLINE"
    else
        sed -i "s/$/ ${key}=${value}/" "$CMDLINE"
    fi
}

set_cmdline_kv "spidev.bufsiz" "65536"
set_cmdline_kv "loglevel" "3"
set_cmdline_kv "consoleblank" "0"
append_cmdline_once "quiet"
append_cmdline_once "logo.nologo"
append_cmdline_once "vt.global_cursor_default=0"
sed -i -E 's/[[:space:]]+/ /g; s/^ //; s/ $//' "$CMDLINE"

echo -e "${YELLOW}[7/8] Enabling services...${NC}"
systemctl daemon-reload
systemctl set-default multi-user.target 2>/dev/null || true
systemctl disable camera-auto    2>/dev/null || true
systemctl enable camera-auto
systemctl disable pigpiod         2>/dev/null || true
systemctl disable uap0            2>/dev/null || true
systemctl disable optocam-hotspot  2>/dev/null || true
systemctl disable optocam-gallery  2>/dev/null || true
systemctl disable hostapd          2>/dev/null || true
systemctl disable dnsmasq          2>/dev/null || true
systemctl disable ModemManager     2>/dev/null || true
systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
systemctl disable systemd-networkd-wait-online.service 2>/dev/null || true
systemctl disable avahi-daemon     2>/dev/null || true
systemctl disable e2scrub_reap     2>/dev/null || true
systemctl disable dphys-swapfile   2>/dev/null || true
systemctl disable bluetooth        2>/dev/null || true
systemctl disable hciuart          2>/dev/null || true
systemctl disable triggerhappy     2>/dev/null || true

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Installation complete!                ${NC}"
echo -e "${GREEN}========================================${NC}"
echo "Camera will start automatically on next boot."
echo "Hotspot SSID:     Optocam Zero"
echo "Hotspot password: 0026opto"
echo "Gallery URL:      http://192.168.4.1  (while connected to hotspot)"
echo ""
echo -e "${YELLOW}Rebooting in 5 seconds... (Ctrl+C to cancel)${NC}"
sleep 5
reboot
