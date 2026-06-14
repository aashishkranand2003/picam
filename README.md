# Optocam Zero Software — 3.5" ILI9486 Edition

Modified from the original Optocam Zero project to run on:

- **Raspberry Pi Zero 2W**
- **Raspberry Pi Camera Module 3** (IMX708, phase-detect autofocus)
- **3.5" SPI display** (ILI9486 controller, 480×320, landscape)

<br>

## What changed from the original

| Area | Original | This version |
|---|---|---|
| Display controller | ST7789 | ILI9486 |
| Resolution | 240×240 | 480×320 |
| Preview fills display | Yes (240×240) | Yes (480×320, full screen) |
| `dtoverlay` for display | `st7789,cs=1,dc=9,...` | None — pure userspace SPI |
| `dtoverlay=spi1-3cs` | Present | Removed |
| GPIO RST / DC / BL | 25 / 9 / 13 | 27 / 25 / 24 |
| Filters | 8 + No Filter | 8 + HDR + No Filter (10 total) |
| HDR capture | No | Yes — 3-exposure merge |
| Splash file | 115 200 B (240×240) | 307 200 B (480×320) |
| Boot time | ~22 s | <10 s (services trimmed, timeouts reduced) |

<br>

## Requirements

- Micro SD card, 16 GB or larger (A2 class recommended)
- A computer with internet access for initial setup

<br>

## Display wiring

If you are using a Waveshare 3.5" SPI HAT or compatible clone that plugs directly onto the 40-pin header, no extra wiring is needed.

For manual wiring:

| Display pin | Raspberry Pi GPIO |
|---|---|
| RST | GPIO 27 |
| DC | GPIO 25 |
| BL | GPIO 24 |
| CS | GPIO 8 (SPI0 CE0) |
| MOSI | GPIO 10 (SPI0 MOSI) |
| SCLK | GPIO 11 (SPI0 SCLK) |
| VCC | 3.3 V or 5 V (check datasheet) |
| GND | GND |

Button and joystick GPIO pins are unchanged from the original Optocam Zero design.

<br>

## Installation

**1. Flash the SD card**

Download Raspberry Pi Imager from [raspberrypi.com/software](https://www.raspberrypi.com/software/). Select **Raspberry Pi Zero 2W** as the device and **Raspberry Pi OS Lite (32-bit) Bookworm** as the OS (under Raspberry Pi OS (other)).

Before flashing, click **Edit Settings** and enter your hostname, username, password, and WiFi credentials. Under Services, enable SSH. Flash the card.

**2. First boot**

Insert the card and power on the Pi. Wait about 1–2 minutes for it to boot and connect to your WiFi.

**3. Connect via SSH**

```
ssh your-username@your-hostname.local
```

Type `yes` at the fingerprint prompt and enter your password.

**4. Run the installer**

```
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/dorukkumkumoglu/optocamzero.git
sudo bash optocamzero/software/install.sh
```

Installation takes 10–15 minutes. The Pi reboots automatically when done and the camera starts on the next boot. Boot-to-preview time is under 10 seconds.

<br>

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

**Display stays blank / wrong colours:**

Check that `dtparam=spi=on` is in `/boot/firmware/config.txt`. Verify RST, DC, BL pins match the values in `optocamzero.py` (GPIO 27, 25, 24). If artefacts appear, reduce SPI speed by changing `spi.max_speed_hz` to `32000000`.

Some 3.5" HATs use the ILI9341 (320×240) or ST7796S (480×320) controller instead of the ILI9486. If the display stays blank after correct wiring, swap the `init_display()` sequence in `optocamzero.py` for the correct controller's sequence.

<br>

## Interface / Controls

Boot-to-preview takes under 10 seconds. Focus is continuous auto (PDAF). Shutter speed and ISO are set to auto.

10 filters are available: Film Standard, Punch, B&W, Deep, Sand, Eterna, TRI-X, Cutout, HDR, No Filter.

### Main camera preview screen

- **Top left** — current white balance mode
- **Top right** — current filter indicator
- **Bottom left** — ISO
- **Bottom right** — shutter speed
- **Bottom centre spinner** — image is saving; do not power off until it disappears
- **Up / down joystick** — cycle through filters (including HDR)
- **Left / right joystick** — cycle white balance mode
- **Shutter button (KEY1)** — capture photo
- **Preview button (KEY2)** — toggle preview on / off

### HDR mode

Select **HDR** using the up/down joystick. When you press the shutter, the camera takes three frames at different exposures (1/250 s, 1/40 s, 1/10 s) and merges them using luminance-weighted blending. HDR capture takes slightly longer than a standard shot. The saving spinner will remain visible while the merge completes in the background.

### Gallery

- **Centre joystick press** — open gallery (opens to most recent image)
- **Left / right joystick** — browse photos (hold for fast scroll)
- **Joystick up** — initiate delete; press up again to confirm, any other button to cancel
- **Shutter button or centre joystick press** — close gallery and return to preview

### Transfer mode (WiFi hotspot)

- **Long-press centre joystick** — toggle transfer mode on/off
- Connect to **Optocam Zero** WiFi (password shown on screen: `0026opto`)
- Open **http://192.168.4.1** in a browser
- Download photos individually or batch-select and download as ZIP
- The dot in the top-right corner turns green when a device is connected

### Triple-press centre joystick

Shows the splash screen. Press any button to dismiss.
