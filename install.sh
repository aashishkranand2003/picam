#!/bin/bash
set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run with sudo: sudo bash install.sh${NC}"
    exit 1
fi

INSTALL_USER=${SUDO_USER:-pi}
INSTALL_HOME=$(getent passwd "$INSTALL_USER" | cut -d: -f6)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  OptoCamZero Installer${NC}"
echo -e "${GREEN}  3.5\" ILI9486 480x320 Edition${NC}"
echo -e "${GREEN}  Raspberry Pi OS Lite 64-bit${NC}"
echo -e "${GREEN}========================================${NC}"
echo "User:     $INSTALL_USER"
echo "Home:     $INSTALL_HOME"
echo ""

# ── Step 1 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[1/9] Installing system packages...${NC}"
apt-get update -q
apt-get install -y hostapd dnsmasq python3-pip python3-flask \
    python3-numpy python3-pil python3-picamera2 git build-essential \
    python3-dev python3-setuptools wget unzip openssh-server

# ── Step 2 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[2/9] Installing Python packages and building pigpio...${NC}"
pip3 install spidev --break-system-packages 2>/dev/null || pip3 install spidev

pip3 install setuptools wheel --break-system-packages 2>/dev/null || true

echo "    Building pigpio from source (required for 64-bit Raspberry Pi OS)..."
PIGPIO_WORK=/tmp/pigpio-build
mkdir -p "$PIGPIO_WORK"
cd "$PIGPIO_WORK"
rm -rf pigpio-master master.zip 2>/dev/null || true

wget -q https://github.com/joan2937/pigpio/archive/master.zip -O master.zip

# -o = overwrite without prompting, -q = quiet
unzip -qo master.zip
cd pigpio-master

# Remove the setup.py install lines so make does not try to use distutils
sed -i \
    -e '/if which python2.*setup.py install/d' \
    -e '/if which python3.*setup.py install/d' \
    Makefile

# Build the C library and daemon; capture output so set -o pipefail is safe
make -j"$(nproc)" > /tmp/pigpio-make.log 2>&1 || { echo "pigpio make failed — see /tmp/pigpio-make.log"; exit 1; }
make install

echo "    Installing Python pigpio module..."
pip3 install --no-build-isolation --break-system-packages . 2>/dev/null || \
pip3 install --break-system-packages . 2>/dev/null || \
echo "    (warning: Python pigpio install had issues; C library/daemon are fine)"

ldconfig

echo "    Creating pigpiod systemd service..."
cat > /etc/systemd/system/pigpiod.service << 'PIGPIO_SERVICE'
[Unit]
Description=pigpio daemon
After=local-fs.target

[Service]
Type=forking
ExecStart=/usr/local/bin/pigpiod
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
PIGPIO_SERVICE

echo "    pigpio build and installation complete."
cd "$SCRIPT_DIR"

# ── Step 3 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[3/9] Copying scripts and assets...${NC}"
cp "$SCRIPT_DIR/scripts/optocamzero.py"    "$INSTALL_HOME/optocamzero.py"
cp "$SCRIPT_DIR/scripts/gallery_server.py" "$INSTALL_HOME/gallery_server.py"
cp "$SCRIPT_DIR/assets/cmunvt.ttf"         "$INSTALL_HOME/cmunvt.ttf"
cp "$SCRIPT_DIR/assets/optocamlogo.svg"    "$INSTALL_HOME/optocamlogo.svg"
cp "$SCRIPT_DIR/assets/splash.raw"         "$INSTALL_HOME/splash.raw"

if [ "$INSTALL_HOME" != "/home/aa" ]; then
    sed -i "s|/home/aa|$INSTALL_HOME|g" "$INSTALL_HOME/optocamzero.py"
    sed -i "s|/home/aa|$INSTALL_HOME|g" "$INSTALL_HOME/gallery_server.py"
fi

mkdir -p "$INSTALL_HOME/photos"
chown -R "$INSTALL_USER:$INSTALL_USER" \
    "$INSTALL_HOME/optocamzero.py" \
    "$INSTALL_HOME/gallery_server.py" \
    "$INSTALL_HOME/cmunvt.ttf" \
    "$INSTALL_HOME/optocamlogo.svg" \
    "$INSTALL_HOME/splash.raw" \
    "$INSTALL_HOME/photos"

# ── Step 4 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[4/9] Installing systemd services...${NC}"
for svc in camera-auto optocam-hotspot optocam-gallery uap0; do
    cp "$SCRIPT_DIR/services/$svc.service" "/etc/systemd/system/$svc.service"
    if [ "$INSTALL_HOME" != "/home/aa" ]; then
        sed -i "s|/home/aa|$INSTALL_HOME|g" "/etc/systemd/system/$svc.service"
        sed -i "s|SUDO_USER=aa|SUDO_USER=$INSTALL_USER|g" "/etc/systemd/system/$svc.service"
    fi
done

# ── Step 5 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[5/9] Configuring hotspot (uap0 virtual AP)...${NC}"
cp "$SCRIPT_DIR/services/hostapd.conf"         "/etc/hostapd/hostapd.conf"
cp "$SCRIPT_DIR/services/dnsmasq-optocam.conf" "/etc/dnsmasq.d/optocam.conf"

systemctl unmask hostapd 2>/dev/null || true
sed -i 's|#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/optocam-unmanaged.conf << 'EOF'
[keyfile]
unmanaged-devices=interface-name:uap0
EOF

# ── Step 6 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[6/9] Configuring /boot/firmware/config.txt...${NC}"
CONFIG=/boot/firmware/config.txt

sed -i 's/^camera_auto_detect=1/camera_auto_detect=0/' "$CONFIG"
sed -i 's/^display_auto_detect=1/display_auto_detect=0/' "$CONFIG"
sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$CONFIG"

add_if_missing() {
    grep -qxF "$1" "$CONFIG" || echo "$1" >> "$CONFIG"
}

add_if_missing "dtparam=spi=on"
add_if_missing "dtparam=i2c_arm=off"
add_if_missing "dtoverlay=imx708"
add_if_missing "dtoverlay=disable-bt"
add_if_missing "arm_boost=1"
add_if_missing "arm_freq=1200"
add_if_missing "over_voltage=2"
add_if_missing "initial_turbo=30"
add_if_missing "boot_delay=0"
add_if_missing "disable_splash=1"
add_if_missing "auto_initramfs=0"
add_if_missing "disable_fw_kms_setup=1"
add_if_missing "disable_overscan=1"

CMDLINE=/boot/firmware/cmdline.txt
if ! grep -q "spidev.bufsiz" "$CMDLINE"; then
    sed -i 's/$/ spidev.bufsiz=65536/' "$CMDLINE"
fi
if ! grep -q "quiet" "$CMDLINE"; then
    sed -i 's/$/ quiet loglevel=3 logo.nologo vt.global_cursor_default=0/' "$CMDLINE"
fi
if ! grep -q "consoleblank" "$CMDLINE"; then
    sed -i 's/$/ consoleblank=0/' "$CMDLINE"
fi

# ── Step 7 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[7/9] Enabling services and trimming boot time...${NC}"
systemctl daemon-reload

echo "    Ensuring SSH is enabled..."
systemctl enable ssh
systemctl start ssh 2>/dev/null || true

systemctl enable pigpiod
systemctl enable uap0
systemctl enable camera-auto

systemctl disable optocam-hotspot             2>/dev/null || true
systemctl disable optocam-gallery             2>/dev/null || true
systemctl disable hostapd                     2>/dev/null || true
systemctl disable dnsmasq                     2>/dev/null || true
systemctl disable ModemManager                2>/dev/null || true
systemctl disable avahi-daemon                2>/dev/null || true
systemctl disable e2scrub_reap                2>/dev/null || true
systemctl disable dphys-swapfile              2>/dev/null || true
systemctl disable bluetooth                   2>/dev/null || true
systemctl disable hciuart                     2>/dev/null || true
systemctl disable triggerhappy                2>/dev/null || true
systemctl disable rpi-eeprom-update           2>/dev/null || true
systemctl disable rsyslog                     2>/dev/null || true
systemctl disable systemd-timesyncd           2>/dev/null || true

# wpa_supplicant is intentionally LEFT ENABLED — it manages wlan0 WiFi.
# NetworkManager-wait-online is also left alone: because camera-auto.service
# no longer Wants=network-online.target, this unit has no effect on boot time.

systemctl mask systemd-rfkill        2>/dev/null || true
systemctl mask systemd-rfkill.socket 2>/dev/null || true

mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/optocam-timeout.conf << 'EOF'
[Manager]
DefaultTimeoutStartSec=10s
DefaultTimeoutStopSec=5s
EOF

# ── Step 8 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[8/9] Installing ILI9486 display driver (LCD-show)...${NC}"

LCD_WORK=/tmp/lcd-show-install
mkdir -p "$LCD_WORK"
cd "$LCD_WORK"
rm -rf LCD-show

git clone https://github.com/goodtft/LCD-show.git
chmod -R 755 LCD-show
cd LCD-show

# Suppress the reboot inside LCD35-show so our script controls the reboot
sed -i 's/^\s*reboot\b.*/echo "LCD-show reboot suppressed"/' ./LCD35-show

bash ./LCD35-show

cd "$SCRIPT_DIR"

# ── Step 9 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[9/9] Finalising...${NC}"

CURRENT_IP=$(hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo "Camera starts automatically on next boot."
echo ""
echo -e "${GREEN}WiFi / SSH${NC}"
echo "  wlan0 stays connected to your router."
if [ -n "$CURRENT_IP" ]; then
    echo "  SSH after reboot:  ssh $INSTALL_USER@$CURRENT_IP"
else
    echo "  SSH after reboot:  ssh $INSTALL_USER@<pi-ip>"
fi
echo ""
echo -e "${GREEN}Hotspot (toggle from camera UI with long-press centre joystick)${NC}"
echo "  SSID:     Optocam Zero"
echo "  Password: 0026opto"
echo "  Gallery:  http://192.168.4.1"
echo ""
echo -e "${YELLOW}Rebooting in 5 seconds... (Ctrl+C to cancel)${NC}"
sleep 5
reboot
