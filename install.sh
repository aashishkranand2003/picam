#!/bin/bash
set -e

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
    python3-dev python3-setuptools wget unzip

# ── Step 2 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[2/9] Installing Python packages and building pigpio...${NC}"
pip3 install spidev --break-system-packages 2>/dev/null || \
pip3 install spidev

# Ensure setuptools and wheel are available for building pigpio
pip3 install setuptools wheel --break-system-packages 2>/dev/null || true

# Build pigpio from source (required for 64-bit Raspberry Pi OS)
echo "    Building pigpio from source (this may take a few minutes)..."
PIGPIO_WORK=/tmp/pigpio-build
mkdir -p "$PIGPIO_WORK"
cd "$PIGPIO_WORK"

# Clean any previous build
rm -rf pigpio master.zip 2>/dev/null || true

wget -q https://github.com/joan2937/pigpio/archive/master.zip -O master.zip
unzip -q master.zip
cd pigpio-master

# Build the C library and daemon (skip the distutils-based Python install)
make -j$(nproc) 2>&1 | tail -3

# Comment out the problematic distutils-based Python install in the Makefile
# (before running make install)
sed -i 's/^\(if which python3; then python3 setup.py install ; fi\)/# \1/' Makefile

# Now run make install without the Python module install
make install

# Install the Python module manually using pip (works with Python 3.13)
echo "    Installing Python pigpio module..."
cd "$PIGPIO_WORK/pigpio-master"
pip3 install --no-build-isolation --break-system-packages . 2>/dev/null || \
pip3 install --break-system-packages . 2>/dev/null || \
echo "    (warning: Python module install had issues, but C library is OK)"

# Update LD library cache for the new C libraries
ldconfig

echo "    pigpio build and installation complete."

# Return to script directory
cd "$SCRIPT_DIR"

# ── Step 3 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[3/9] Copying scripts and assets...${NC}"
cp "$SCRIPT_DIR/scripts/optocamzero.py"    "$INSTALL_HOME/optocamzero.py"
cp "$SCRIPT_DIR/scripts/gallery_server.py" "$INSTALL_HOME/gallery_server.py"
cp "$SCRIPT_DIR/assets/cmunvt.ttf"         "$INSTALL_HOME/cmunvt.ttf"
cp "$SCRIPT_DIR/assets/optocamlogo.svg"    "$INSTALL_HOME/optocamlogo.svg"
cp "$SCRIPT_DIR/assets/splash.raw"         "$INSTALL_HOME/splash.raw"

sed -i "s|/home/dkumkum|$INSTALL_HOME|g" "$INSTALL_HOME/optocamzero.py"
sed -i "s|/home/dkumkum|$INSTALL_HOME|g" "$INSTALL_HOME/gallery_server.py"

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
    sed -i "s|/home/dkumkum|$INSTALL_HOME|g" "/etc/systemd/system/$svc.service"
    sed -i "s|dkumkum|$INSTALL_USER|g"       "/etc/systemd/system/$svc.service"
done

# ── Step 5 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[5/9] Configuring hotspot (uap0 virtual AP)...${NC}"
cp "$SCRIPT_DIR/services/hostapd.conf"         "/etc/hostapd/hostapd.conf"
cp "$SCRIPT_DIR/services/dnsmasq-optocam.conf" "/etc/dnsmasq.d/optocam.conf"

systemctl unmask hostapd
sed -i 's|#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

# Tell NetworkManager to leave only the virtual AP interface (uap0) alone.
# wlan0 stays fully managed so the Pi keeps its WiFi connection for SSH.
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

# SPI for the ILI9486 display (userspace driver — no display dtoverlay needed)
add_if_missing "dtparam=spi=on"
add_if_missing "dtparam=i2c_arm=off"

# Camera Module 3
add_if_missing "dtoverlay=imx708"

# Disable Bluetooth to free UART and reduce boot time
add_if_missing "dtoverlay=disable-bt"

# CPU performance — Zero 2W can sustain 1200 MHz reliably
add_if_missing "arm_boost=1"
add_if_missing "arm_freq=1200"
add_if_missing "over_voltage=2"

# Boot speed
add_if_missing "initial_turbo=30"
add_if_missing "boot_delay=0"
add_if_missing "disable_splash=1"
add_if_missing "auto_initramfs=0"
add_if_missing "disable_fw_kms_setup=1"
add_if_missing "disable_overscan=1"

# Larger SPI buffer for fast full-frame transfers
CMDLINE=/boot/firmware/cmdline.txt
if ! grep -q "spidev.bufsiz" "$CMDLINE"; then
    sed -i 's/$/ spidev.bufsiz=65536/' "$CMDLINE"
fi

# Suppress kernel boot messages and console blank
if ! grep -q "quiet" "$CMDLINE"; then
    sed -i 's/$/ quiet loglevel=3 logo.nologo vt.global_cursor_default=0/' "$CMDLINE"
fi
if ! grep -q "consoleblank" "$CMDLINE"; then
    sed -i 's/$/ consoleblank=0/' "$CMDLINE"
fi

# ── Step 7 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[7/9] Enabling services and trimming boot time...${NC}"
systemctl daemon-reload
systemctl enable pigpiod
systemctl enable uap0
systemctl enable camera-auto
systemctl disable optocam-hotspot             2>/dev/null || true
systemctl disable optocam-gallery             2>/dev/null || true
systemctl disable hostapd                     2>/dev/null || true
systemctl disable dnsmasq                     2>/dev/null || true
systemctl disable ModemManager                2>/dev/null || true
systemctl disable NetworkManager-wait-online  2>/dev/null || true
systemctl disable avahi-daemon                2>/dev/null || true
systemctl disable e2scrub_reap                2>/dev/null || true
systemctl disable dphys-swapfile              2>/dev/null || true
systemctl disable bluetooth                   2>/dev/null || true
systemctl disable hciuart                     2>/dev/null || true
systemctl disable triggerhappy                2>/dev/null || true
systemctl disable rpi-eeprom-update           2>/dev/null || true
systemctl disable rsyslog                     2>/dev/null || true
systemctl disable systemd-timesyncd           2>/dev/null || true
# NOTE: wpa_supplicant is intentionally left ENABLED so wlan0 stays connected
# to your WiFi network and SSH remains accessible after reboot.

systemctl mask    systemd-rfkill              2>/dev/null || true
systemctl mask    systemd-rfkill.socket       2>/dev/null || true

# Reduce systemd default timeout so slow units don't stall boot
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/optocam-timeout.conf << 'EOF'
[Manager]
DefaultTimeoutStartSec=10s
DefaultTimeoutStopSec=5s
EOF

# ── Step 8 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[8/9] Installing ILI9486 display driver (LCD-show)...${NC}"
echo    "      This configures the kernel framebuffer; reboot happens after."

# Work in /tmp so we don't pollute the repo directory
LCD_WORK=/tmp/lcd-show-install
mkdir -p "$LCD_WORK"
cd "$LCD_WORK"

# Clean any previous attempt
rm -rf LCD-show

git clone https://github.com/goodtft/LCD-show.git
chmod -R 755 LCD-show
cd LCD-show

# The LCD35-show script ends with a 'reboot' call.
# We patch that line out so THIS script controls when the reboot happens
# (after we have printed the completion message below).
sed -i 's/^\s*reboot\b.*/echo "[LCD-show] reboot suppressed — installer will reboot"/' ./LCD35-show

# Run the patched driver installer
bash ./LCD35-show

cd "$SCRIPT_DIR"

# ── Step 9 ────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}[9/9] Finalising...${NC}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo "Camera starts automatically on next boot."
echo ""
echo "WiFi / SSH"
echo "  wlan0 stays connected to your router — SSH works after reboot."
echo "  Connect to your router's network and SSH in as usual."
echo ""
echo "Hotspot (for photo transfer)"
echo "  SSID:     Optocam Zero"
echo "  Password: 0026opto"
echo "  Gallery:  http://192.168.4.1  (while connected to hotspot)"
echo ""
echo -e "${YELLOW}Rebooting in 5 seconds... (Ctrl+C to cancel)${NC}"
sleep 5
reboot
