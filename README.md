# Optocam Zero — 3.5" ILI9486 Edition

Modified from the original Optocam Zero project to run on:

- **Raspberry Pi Zero 2W**
- **Raspberry Pi Camera Module 3** (IMX708, PDAF)
- **3.5" SPI display** (ILI9486 controller, 480×320, landscape)

---

## Hardware wiring

If you are using a Waveshare 3.5" SPI HAT or a compatible clone that plugs directly onto the 40-pin GPIO header, the pin mapping is already wired correctly on the HAT and no extra connections are needed.

If you are wiring the display manually, connect as follows:

| Display pin | Raspberry Pi GPIO |
|-------------|-------------------|
| RST         | GPIO 27           |
| DC          | GPIO 25           |
| BL          | GPIO 24           |
| MOSI        | GPIO 10 (SPI0)    |
| SCLK        | GPIO 11 (SPI0)    |
| CS          | GPIO 8  (SPI0 CE0)|
| VCC         | 3.3V or 5V (check your display datasheet) |
| GND         | GND               |

Button and joystick GPIO pins are unchanged from the original Optocam Zero design.

---

## Requirements

- Micro SD card, 16 GB or larger (A2 class recommended)
- A computer with internet access for initial setup

---

## Installation

**1. Flash the SD card**

Download Raspberry Pi Imager from [raspberrypi.com/software](https://www.raspberrypi.com/software/). Select **Raspberry Pi Zero 2W** as the device and **Raspberry Pi OS Lite (32-bit, Bookworm)** as the OS (found under *Raspberry Pi OS (other)*).

Before flashing, click **Edit Settings** and set your hostname, username, password, and Wi-Fi credentials. Under the Services tab, enable SSH. Flash the card.

**2. First boot**

Insert the card and power on the Pi. Wait 1–2 minutes for it to boot and connect to your Wi-Fi.

**3. Connect via SSH**

```
ssh your-username@your-hostname.local
```

Type `yes` at the fingerprint prompt and enter your password.

**4. Clone and run the installer**

```
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/aashishkranand2003/picam.git
sudo bash picam/install.sh
```

Installation takes 10–15 minutes. The Pi reboots automatically when complete and the camera starts on the next boot.

The installer also clones the GoodTFT `LCD-show` repository and runs `LCD35-show` during setup so the display driver is configured automatically.

---

## Fast boot profile

The installer configures the camera app as the first regular boot service. It starts before `basic.target`, waits briefly for `/dev/spidev0.0`, shows the SPI display splash, starts Picamera2 preview, and only then lets normal services such as Wi-Fi, SSH, NetworkManager, and hotspot-related units continue.

On a Raspberry Pi Zero 2W with Raspberry Pi OS Lite and a fast A2 card, this is intended to reach camera preview in roughly the 10 second range. Actual boot time still depends on SD card speed, camera detection, first-boot resizing, and any extra services installed later.

---

## Display note

The installer drives the ILI9486 display through the GoodTFT `LCD-show` vendor setup and then enables SPI (`dtparam=spi=on`) for the rest of the software stack. There is no manual `dtoverlay` step in this project.

The installer also loads the SPI kernel modules at boot, sets `spidev.bufsiz=65536`, and sets the display GPIO defaults:

- **BL:** GPIO 24, output high
- **DC:** GPIO 25, output high
- **RST:** GPIO 27, output high

It also removes the old `st7789`, `spi1-3cs`, and `vc4-kms-v3d`/`vc4-fkms-v3d` display overlays because this build drives the ILI9486 directly from Python over SPI.

The installer clones [GoodTFT LCD-show](https://github.com/goodtft/LCD-show) and runs `LCD35-show` as part of the display setup.

---

## Troubleshooting

**SSH shows "host key changed":**
```
ssh-keygen -R your-hostname.local
```

**Camera does not start after reboot:**
```
sudo systemctl status camera-auto.service
sudo journalctl -u camera-auto.service -n 50
```

**Display shows nothing / wrong colours:**
Check that `dtparam=spi=on` is present in `/boot/firmware/config.txt` and that your DC, RST, and BL pins match the values in `optocamzero.py` (GPIO 25, 27, 24 respectively). Drop SPI speed to 32 MHz by changing `spi.max_speed_hz` in the script if you see artefacts.

**ILI9341 or ST7796S display variant:**
Some 3.5" HATs use the ILI9341 (320×240) or ST7796S (480×320) controller instead of the ILI9486. The `init_display()` function in `optocamzero.py` contains the ILI9486 sequence. Swap it for the correct init sequence for your controller if the display stays blank after a correct wiring check.

---

## Controls

Controls are identical to the original Optocam Zero.

### Camera preview screen
- **Top-left:** current white balance mode (cycle with left/right joystick)
- **Top-right:** current filter indicator
- **Bottom-left:** ISO
- **Bottom-right:** shutter speed
- **Bottom-centre spinner:** appears while an image is saving — do not power off until it disappears
- **Up/down joystick:** cycle through filters
- **Shutter button (KEY1):** capture photo
- **Preview button (KEY2):** toggle preview on/off

### Gallery
- **Centre joystick press:** open gallery
- **Left/right joystick:** browse photos (hold for fast scroll)
- **Joystick up:** initiate delete — press up again to confirm, any other button to cancel
- **Shutter button or centre joystick press:** close gallery and return to preview

### Transfer mode (Wi-Fi hotspot)
- **Long-press centre joystick:** toggle transfer mode on/off
- Connect to the **Optocam Zero** Wi-Fi network (password shown on screen: `0026opto`)
- Open **http://192.168.4.1** in a browser
- Download individual photos or batch-select and download as ZIP
- The status dot in the top-right corner turns green when a device is connected

### Triple-press centre joystick
Activates the splash screen. Press any button to dismiss.

---

## What changed from the original

| Area | Original | This version |
|------|----------|--------------|
| Display controller | ST7789 | ILI9486 |
| Resolution | 240×240 | 480×320 |
| Viewfinder area | Full 240×240 | 320×320 centred (80 px black bars left/right) |
| Splash file | 115 200 bytes (240×240 RGB565) | 307 200 bytes (480×320 RGB565) |
| `dtoverlay` for display | `st7789,cs=1,dc=9,rst=25,bl=13,width=240,height=240` | None (pure userspace SPI driver) |
| `dtoverlay=spi1-3cs` | Present | Removed (not needed without kernel display driver) |
| GPIO RST | 25 | 27 |
| GPIO DC | 9 | 25 |
| GPIO BL | 13 | 24 |
| HUD layout | 240×240 canvas | 480×320 canvas, HUD anchored to viewfinder edges |
| Camera config | Unchanged | Unchanged (IMX708 is the same module) |
