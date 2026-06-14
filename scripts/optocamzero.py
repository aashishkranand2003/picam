#!/usr/bin/env python3
import sys
import time
_script_start = time.time()
def log(msg):
    sys.stderr.write(f"[{time.time() - _script_start:.2f}s] {msg}\n")
    sys.stderr.flush()
log("Script file started")
import RPi.GPIO as GPIO
log("GPIO imported")
import spidev
log("spidev imported")
import threading
log("threading imported")
import os
log("os imported")
from PIL import Image
log("PIL imported")
import numpy as np
log("numpy imported")
import gc
import subprocess
log("All imports done")


def systemd_notify(*messages):
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return
    try:
        import socket
        addr = "\0" + notify_socket[1:] if notify_socket.startswith("@") else notify_socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall("\n".join(messages).encode())
    except Exception as exc:
        log(f"systemd notify failed: {exc}")


GPIO.setwarnings(False)

RST_PIN = 27
DC_PIN  = 25
BL_PIN  = 24

BUTTON_CAPTURE = 21
BUTTON_PREVIEW = 20
JOYSTICK_LEFT  = 5
JOYSTICK_RIGHT = 26
JOYSTICK_PRESS = 13
JOYSTICK_UP    = 6
JOYSTICK_DOWN  = 19

DISP_W = 480
DISP_H = 320

GPIO.setmode(GPIO.BCM)
GPIO.setup(RST_PIN, GPIO.OUT)
GPIO.setup(DC_PIN,  GPIO.OUT)
GPIO.setup(BL_PIN,  GPIO.OUT)
_backlight_pwm = GPIO.PWM(BL_PIN, 1000)
_backlight_pwm.start(100)
GPIO.setup(BUTTON_PREVIEW,  GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_CAPTURE,  GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(JOYSTICK_LEFT,   GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(JOYSTICK_RIGHT,  GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(JOYSTICK_PRESS,  GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(JOYSTICK_UP,     GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(JOYSTICK_DOWN,   GPIO.IN, pull_up_down=GPIO.PUD_UP)

spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 64000000
spi.mode = 0
spi.bits_per_word = 8

camera_lock  = threading.RLock()
display_lock = threading.Lock()


class CameraConfigCache:
    def __init__(self, picam2):
        self.preview_config = picam2.create_preview_configuration(
            main={"size": (DISP_W, DISP_H), "format": "RGB888"},
            buffer_count=3,
            queue=False,
            controls={
                "AfMode": 2,
                "AfSpeed": 1,
                "FrameDurationLimits": (100, 25000),
            },
        )
        self.capture_config = picam2.create_still_configuration(
            main={"size": (2592, 2592), "format": "RGB888"},
            buffer_count=2,
        )
        self.hdr_configs = [
            picam2.create_still_configuration(
                main={"size": (2592, 2592), "format": "RGB888"},
                buffer_count=2,
                controls={"ExposureTime": ev, "AeEnable": False},
            )
            for ev in [4000, 25000, 100000]
        ]


config_cache = None
FONT_PATH = "/home/dkumkum/cmunvt.ttf"

_shadow_cache = {}


def make_text_shadow(text, x, y, font):
    from PIL import ImageDraw, ImageFilter
    shadow = Image.new("RGBA", (DISP_W, DISP_H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x, y), text, font=font, fill=(0, 0, 0, 200))
    return shadow.filter(ImageFilter.GaussianBlur(radius=4))


_STANDARD_ISO = [100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1000, 1250, 1600]

AWB_MODES = [
    (1, "Tungsten",    "TNG"),
    (2, "Fluorescent", "FLR"),
    (3, "Indoor",      "IND"),
    (4, "Daylight",    "DAY"),
    (5, "Cloudy",      "CLD"),
]

_STANDARD_SHUTTERS = [
    (125, "1/8000"), (156, "1/6400"), (200, "1/5000"), (250, "1/4000"),
    (313, "1/3200"), (400, "1/2500"), (500, "1/2000"), (625, "1/1600"),
    (800, "1/1250"), (1000, "1/1000"), (1250, "1/800"), (1563, "1/640"),
    (2000, "1/500"), (2500, "1/400"), (3125, "1/320"), (4000, "1/250"),
    (5000, "1/200"), (6250, "1/160"), (8000, "1/125"), (10000, "1/100"),
    (12500, "1/80"), (16667, "1/60"), (20000, "1/50"), (25000, "1/40"),
    (33333, "1/30"), (40000, "1/25"), (50000, "1/20"), (66667, "1/15"),
    (100000, "1/10"), (125000, "1/8"), (166667, "1/6"), (200000, "1/5"),
    (250000, "1/4"), (333333, "1/3"), (500000, "1/2"), (1000000, "1\""),
]


def nearest_standard_iso(gain):
    iso = gain * 100
    return min(_STANDARD_ISO, key=lambda s: abs(s - iso))


def nearest_standard_shutter(exp_us):
    return min(_STANDARD_SHUTTERS, key=lambda s: abs(s[0] - exp_us))[1]


def get_cached_shadow(key, text, x, y, font):
    if _shadow_cache.get(key) != text:
        _shadow_cache[key] = text
        _shadow_cache[key + "_img"] = make_text_shadow(text, x, y, font)
    return _shadow_cache[key + "_img"]


_indicator_cache = {}


def get_filter_indicator(filter_name):
    if filter_name in _indicator_cache:
        return _indicator_cache[filter_name]
    from PIL import ImageDraw, ImageFilter
    pad = 14
    r   = 22
    cx  = DISP_W - pad - r
    cy  = pad + r
    font = load_font(28)

    shadow = Image.new("RGBA", (DISP_W, DISP_H), (0, 0, 0, 0))
    white  = Image.new("RGBA", (DISP_W, DISP_H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    wd = ImageDraw.Draw(white)

    _PILL_LABELS = {
        "B&W": "B&W", "TRI-X": "TX", "Film Standard": "FS", "HDR": "HDR"
    }

    if filter_name in _PILL_LABELS:
        label  = _PILL_LABELS[filter_name]
        tb     = wd.textbbox((0, 0), label, font=font)
        h_pad  = 12
        pill_h = r * 2
        pill_w = (tb[2] - tb[0]) + h_pad * 2
        x1, y0 = DISP_W - pad, pad
        x0, y1 = x1 - pill_w, y0 + pill_h
        cr  = pill_h // 2
        pcx = (x0 + x1) // 2
        pcy = (y0 + y1) // 2
        tx  = pcx - (tb[0] + tb[2]) // 2
        ty  = pcy - (tb[1] + tb[3]) // 2
        sd.rounded_rectangle([x0, y0, x1, y1], radius=cr, outline=(0, 0, 0, 200), width=2)
        sd.text((tx, ty), label, font=font, fill=(0, 0, 0, 200))
        wd.rounded_rectangle([x0, y0, x1, y1], radius=cr, outline=(255, 255, 255, 255), width=1)
        wd.text((tx, ty), label, font=font, fill=(255, 255, 255, 255))
    else:
        label = filter_name[0]
        tb = wd.textbbox((0, 0), label, font=font)
        tx = cx - (tb[0] + tb[2]) // 2
        ty = cy - (tb[1] + tb[3]) // 2
        if label in ("D", "P", "L", "N"):
            tx += 1
        sd.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(0, 0, 0, 200), width=2)
        sd.text((tx, ty), label, font=font, fill=(0, 0, 0, 200))
        wd.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(255, 255, 255, 255), width=1)
        wd.text((tx, ty), label, font=font, fill=(255, 255, 255, 255))

    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))
    layer  = Image.alpha_composite(shadow, white)
    _indicator_cache[filter_name] = layer
    return layer


_font_cache = {}


def load_font(size):
    if size in _font_cache:
        return _font_cache[size]
    try:
        from PIL import ImageFont
        _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
    except:
        from PIL import ImageFont
        _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


FILTERS = ["Film Standard", "Punch", "B&W", "Deep", "Sand", "Eterna", "TRI-X", "Cutout", "HDR", "No Filter"]


def _make_lut(points):
    x = [p[0] for p in points]
    y = [p[1] for p in points]
    return np.interp(np.arange(256), x, y).clip(0, 255).astype(np.uint8)


_BASE_CURVES = {
    "B&W":           _make_lut([(0,0),(64,16),(128,160),(192,242),(255,255)]),
    "Punch":         _make_lut([(0,0),(64,52),(128,148),(192,212),(255,242)]),
    "Sand":          _make_lut([(0,0),(64,50),(128,132),(192,205),(255,255)]),
    "Deep":          _make_lut([(0,30),(64,70),(128,152),(192,222),(255,255)]),
    "Eterna":        _make_lut([(0,30),(64,78),(128,128),(192,172),(255,215)]),
    "Film Standard": _make_lut([(0,18),(64,55),(128,140),(192,210),(255,252)]),
}

_CHANNEL_LUTS = {}
_v = np.arange(256, dtype=np.float32)

_CHANNEL_LUTS["B&W"] = (
    _BASE_CURVES["B&W"], _BASE_CURVES["B&W"], _BASE_CURVES["B&W"],
)

_pc = _BASE_CURVES["Punch"].astype(np.float32)
_punch_shadow_blue = np.maximum(0.0, 65.0 * (1.0 - _v / 105.0))
_CHANNEL_LUTS["Punch"] = (
    np.clip(_pc * 1.05, 0, 255).astype(np.uint8),
    np.clip(_pc * 1.02, 0, 255).astype(np.uint8),
    np.clip(_pc + _punch_shadow_blue, 0, 255).astype(np.uint8),
)

_sc = _BASE_CURVES["Sand"].astype(np.float32)
_CHANNEL_LUTS["Sand"] = (
    np.clip(_sc * 1.08, 0, 255).astype(np.uint8),
    np.clip(_sc * 0.92, 0, 255).astype(np.uint8),
    np.clip(_sc * 0.55, 0, 255).astype(np.uint8),
)

_dc = _BASE_CURVES["Deep"].astype(np.float32)
_CHANNEL_LUTS["Deep"] = (
    np.clip(_dc * 0.55, 0, 255).astype(np.uint8),
    np.clip(_dc * 0.70, 0, 255).astype(np.uint8),
    np.clip(_dc * 1.35, 0, 255).astype(np.uint8),
)

_et = _BASE_CURVES["Eterna"].astype(np.float32)
_CHANNEL_LUTS["Eterna"] = (
    np.clip(_et * 0.96, 0, 255).astype(np.uint8),
    np.clip(_et * 1.00, 0, 255).astype(np.uint8),
    np.clip(_et * 1.05, 0, 255).astype(np.uint8),
)

_fs = _BASE_CURVES["Film Standard"].astype(np.float32)
_CHANNEL_LUTS["Film Standard"] = (
    np.clip(_fs * 0.95, 0, 255).astype(np.uint8),
    np.clip(_fs * 1.02, 0, 255).astype(np.uint8),
    np.clip(_fs * 1.08, 0, 255).astype(np.uint8),
)

_co = np.zeros(256, dtype=np.uint8)
_co[65:130] = 128
_co[130:]   = 255
_CHANNEL_LUTS["Cutout"] = (_co, _co, _co)

_TRITON_SHADOW    = np.array([0,   0,   0],   dtype=np.float32)
_TRITON_MID       = np.array([242, 183,  8],  dtype=np.float32)
_TRITON_HIGHLIGHT = np.array([35,  155, 60],  dtype=np.float32)
_trix_lut = np.zeros((256, 3), dtype=np.float32)
_TRIX_BLACK = 0.18
_TRIX_SPLIT = 0.38
for _i in range(256):
    _t = _i / 255.0
    if _t <= _TRIX_BLACK:
        _trix_lut[_i] = _TRITON_SHADOW
    elif _t <= _TRIX_SPLIT:
        _t2 = (_t - _TRIX_BLACK) / (_TRIX_SPLIT - _TRIX_BLACK)
        _trix_lut[_i] = _TRITON_SHADOW + (_TRITON_MID - _TRITON_SHADOW) * _t2
    else:
        _t2 = (_t - _TRIX_SPLIT) / (1.0 - _TRIX_SPLIT)
        _trix_lut[_i] = _TRITON_MID + (_TRITON_HIGHLIGHT - _TRITON_MID) * _t2
_TRIX_LUT = np.clip(_trix_lut, 0, 255).astype(np.uint8)

_GRAIN_TABLE_SIZE = 1024
_grain_tables: dict = {}
_grain_rng = np.random.default_rng(0)


def _get_grain_table(intensity: int) -> np.ndarray:
    if intensity not in _grain_tables:
        _grain_tables[intensity] = _grain_rng.integers(
            -intensity, intensity + 1,
            (_GRAIN_TABLE_SIZE, _GRAIN_TABLE_SIZE),
            dtype=np.int16,
        )
    return _grain_tables[intensity]


def _apply_grain(arr: np.ndarray, intensity: int) -> np.ndarray:
    h, w = arr.shape[:2]
    table = _get_grain_table(intensity)
    dy = np.random.randint(0, _GRAIN_TABLE_SIZE)
    dx = np.random.randint(0, _GRAIN_TABLE_SIZE)
    rows = (np.arange(dy, dy + h) % _GRAIN_TABLE_SIZE)[:, np.newaxis]
    cols = (np.arange(dx, dx + w) % _GRAIN_TABLE_SIZE)[np.newaxis, :]
    grain = table[rows, cols]
    arr16 = arr.astype(np.int16)
    arr16 += grain[:, :, np.newaxis]
    return np.clip(arr16, 0, 255).astype(np.uint8)


_GRAIN = {
    "B&W": 27, "Punch": 22, "Sand": 22, "Deep": 22,
    "Cutout": 22, "TRI-X": 15, "Eterna": 15, "Film Standard": 18,
}

_FILM_ISP = {
    "No Filter":     {"Saturation": 1.0,  "Contrast": 1.0, "Brightness": 0.0},
    "B&W":           {"Saturation": 0.0,  "Contrast": 1.0, "Brightness": 0.0},
    "Punch":         {"Saturation": 1.3,  "Contrast": 1.0, "Brightness": -0.05},
    "Sand":          {"Saturation": 0.45, "Contrast": 1.0, "Brightness": 0.0},
    "Deep":          {"Saturation": 0.6,  "Contrast": 1.0, "Brightness": 0.0},
    "Cutout":        {"Saturation": 0.0,  "Contrast": 1.0, "Brightness": 0.0},
    "TRI-X":         {"Saturation": 1.0,  "Contrast": 1.0, "Brightness": 0.0},
    "Eterna":        {"Saturation": 0.75, "Contrast": 1.0, "Brightness": 0.0},
    "Film Standard": {"Saturation": 0.85, "Contrast": 1.0, "Brightness": 0.0},
    "HDR":           {"Saturation": 1.1,  "Contrast": 1.0, "Brightness": 0.0},
}


def _merge_hdr(images):
    arrays = [np.array(img, dtype=np.float32) for img in images]
    dark, mid, bright = arrays[0], arrays[1], arrays[2]

    luma_dark   = 0.299*dark[:,:,0]   + 0.587*dark[:,:,1]   + 0.114*dark[:,:,2]
    luma_mid    = 0.299*mid[:,:,0]    + 0.587*mid[:,:,1]     + 0.114*mid[:,:,2]
    luma_bright = 0.299*bright[:,:,0] + 0.587*bright[:,:,1]  + 0.114*bright[:,:,2]

    def _well_exposed(luma):
        return np.exp(-((luma - 128.0) ** 2) / (2 * 60.0 ** 2)).astype(np.float32)

    w0 = _well_exposed(luma_dark)   + 1e-6
    w1 = _well_exposed(luma_mid)    + 1e-6
    w2 = _well_exposed(luma_bright) + 1e-6
    total = w0 + w1 + w2

    w0 = (w0 / total)[:,:, np.newaxis]
    w1 = (w1 / total)[:,:, np.newaxis]
    w2 = (w2 / total)[:,:, np.newaxis]

    merged = w0 * dark + w1 * mid + w2 * bright

    luma_merged = 0.299*merged[:,:,0] + 0.587*merged[:,:,1] + 0.114*merged[:,:,2]
    luma_target = np.clip(luma_merged * 1.05, 0, 255)
    scale = np.where(luma_merged > 1, luma_target / luma_merged, 1.0)[:,:, np.newaxis]
    merged = merged * scale

    return Image.fromarray(np.clip(merged, 0, 255).astype(np.uint8))


def _apply_filter_by_name(image, name, apply_grain=True):
    if name in ("No Filter", "HDR"):
        return image
    arr = np.array(image, dtype=np.uint8)
    if name == "TRI-X":
        luma = (arr[:,:,0].astype(np.uint32) * 299 +
                arr[:,:,1].astype(np.uint32) * 587 +
                arr[:,:,2].astype(np.uint32) * 114 + 500) // 1000
        arr = _TRIX_LUT[luma.clip(0, 255).astype(np.uint8)]
    elif name == "Cutout":
        p = _co[np.array(image.convert('L'), dtype=np.uint8)]
        arr = np.stack([p, p, p], axis=2)
    else:
        r_lut, g_lut, b_lut = _CHANNEL_LUTS[name]
        arr[:,:,0] = r_lut[arr[:,:,0]]
        arr[:,:,1] = g_lut[arr[:,:,1]]
        arr[:,:,2] = b_lut[arr[:,:,2]]
    grain = _GRAIN.get(name, 0) if apply_grain else 0
    if grain:
        arr = _apply_grain(arr, grain)
    return Image.fromarray(arr)


def apply_filter(image):
    return _apply_filter_by_name(image, FILTERS[filter_index], apply_grain=False)


def send_command(cmd):
    GPIO.output(DC_PIN, GPIO.LOW)
    spi.xfer([cmd])


def send_data(data):
    GPIO.output(DC_PIN, GPIO.HIGH)
    chunk_size = 65536
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        try:
            spi.writebytes2(chunk)
        except AttributeError:
            spi.writebytes(chunk)


def _send_byte(b):
    GPIO.output(DC_PIN, GPIO.HIGH)
    spi.xfer([b])


def init_display():
    print("Initializing ILI9486 3.5\" display (480x320)...")
    GPIO.output(RST_PIN, GPIO.HIGH); time.sleep(0.05)
    GPIO.output(RST_PIN, GPIO.LOW);  time.sleep(0.05)
    GPIO.output(RST_PIN, GPIO.HIGH); time.sleep(0.12)

    send_command(0x01); time.sleep(0.12)
    send_command(0x11); time.sleep(0.12)

    send_command(0x3A); _send_byte(0x55)

    send_command(0x36); _send_byte(0x28)

    send_command(0xC0); _send_byte(0x19); _send_byte(0x1A)
    send_command(0xC1); _send_byte(0x45); _send_byte(0x00)
    send_command(0xC2); _send_byte(0x33)
    send_command(0xC5); _send_byte(0x00); _send_byte(0x28)

    send_command(0xE0)
    for b in [0x1F,0x25,0x22,0x0B,0x06,0x0A,0x4E,0xC6,0x39,0x00,0x00,0x00,0x00,0x00,0x00]:
        _send_byte(b)

    send_command(0xE1)
    for b in [0x1F,0x3F,0x3F,0x0F,0x1F,0x0F,0x46,0x49,0x31,0x05,0x09,0x03,0x1C,0x1A,0x00]:
        _send_byte(b)

    send_command(0x29)
    time.sleep(0.05)
    print("ILI9486 ready.")


def set_backlight(state):
    _backlight_pwm.ChangeDutyCycle(100 if state else 0)


def set_backlight_brightness(pct):
    pct = max(0, min(100, pct))
    _backlight_pwm.ChangeDutyCycle(pct)


def _set_window(x0, y0, x1, y1):
    send_command(0x2A)
    GPIO.output(DC_PIN, GPIO.HIGH)
    spi.xfer([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
    send_command(0x2B)
    GPIO.output(DC_PIN, GPIO.HIGH)
    spi.xfer([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])
    send_command(0x2C)


def clear_display():
    with display_lock:
        _set_window(0, 0, DISP_W - 1, DISP_H - 1)
        send_data(bytearray(DISP_W * DISP_H * 2))


_CONTRAST_LUT = np.clip(
    (np.arange(256, dtype=np.float32) - 128) * 1.15 + 123,
    0, 255,
).astype(np.uint8)


def convert_to_rgb565(image):
    rgb_array = np.frombuffer(image.tobytes(), dtype=np.uint8).reshape(
        (image.height, image.width, 3)
    )
    rgb_array = _CONTRAST_LUT[rgb_array]
    r = rgb_array[:,:,0].astype(np.uint16) & 0xF8
    g = rgb_array[:,:,1].astype(np.uint16) & 0xFC
    b = rgb_array[:,:,2].astype(np.uint16) & 0xF8
    rgb565 = (r << 8) | (g << 3) | (b >> 3)
    return rgb565.astype('>u2').tobytes()


def display_image(image):
    with display_lock:
        _set_window(0, 0, DISP_W - 1, DISP_H - 1)
        send_data(convert_to_rgb565(image))


def overlay_capture_dot(base_image):
    img_array = np.array(base_image)
    dot_center = (DISP_W - 20, 20)
    dot_radius  = 8
    y, x = np.ogrid[:DISP_H, :DISP_W]
    mask = (x - dot_center[0])**2 + (y - dot_center[1])**2 <= dot_radius**2
    img_array[mask] = [0, 255, 0]
    border = (
        ((x - dot_center[0])**2 + (y - dot_center[1])**2 <= (dot_radius+1)**2) &
        ((x - dot_center[0])**2 + (y - dot_center[1])**2 >  (dot_radius-1)**2)
    )
    img_array[border] = [255, 255, 255]
    return Image.fromarray(img_array)


def show_splash():
    splash_path = "/home/dkumkum/splash.raw"
    expected_bytes = DISP_W * DISP_H * 2
    if os.path.exists(splash_path):
        with open(splash_path, "rb") as f:
            data = f.read()
        if len(data) == expected_bytes:
            with display_lock:
                _set_window(0, 0, DISP_W - 1, DISP_H - 1)
                send_data(data)
            return
    img = Image.new("RGB", (DISP_W, DISP_H), (0, 0, 0))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    font = load_font(32)
    txt  = "Optocam Zero"
    b    = draw.textbbox((0, 0), txt, font=font)
    draw.text(((DISP_W - (b[2]-b[0])) // 2, (DISP_H - (b[3]-b[1])) // 2),
              txt, font=font, fill=(255, 255, 255))
    display_image(img)


def show_transfer_mode_screen():
    from PIL import ImageDraw
    img  = Image.new("RGB", (DISP_W, DISP_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    font_title = load_font(22)
    font_label = load_font(20)
    font_value = load_font(26)
    font_hint  = load_font(20)

    try:
        result = subprocess.run(
            ["/usr/sbin/iw", "dev", "uap0", "station", "dump"],
            capture_output=True, text=True, timeout=1,
        )
        device_count     = result.stdout.count("Station ")
        device_connected = device_count > 0
    except Exception:
        device_count     = 0
        device_connected = False

    dot_visible = device_connected or (int(time.time() * 2) % 2 == 0)
    dot_color   = (60, 200, 80) if device_connected else (90, 90, 90)
    dot_r = 6

    title   = "Transfer Mode"
    title_y = 18
    draw.text((24, title_y), title, font=font_title, fill=(160, 160, 160))
    tb      = draw.textbbox((0, 0), title, font=font_title)
    dot_cy  = title_y + (tb[1] + tb[3]) // 2
    dot_cx  = DISP_W - 24
    if dot_visible:
        draw.ellipse(
            [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
            fill=dot_color,
        )
    if device_connected:
        count_str = str(device_count)
        cb        = draw.textbbox((0, 0), count_str, font=font_title)
        count_w   = cb[2] - cb[0]
        count_x   = dot_cx - dot_r - count_w - 8
        count_y   = dot_cy - (cb[3] - cb[1]) // 2 - cb[1]
        draw.text((count_x, count_y), count_str, font=font_title, fill=dot_color)

    draw.line([(8, 56), (DISP_W - 8, 56)], fill=(40, 40, 40), width=1)

    col_x = [24, 190, 340]
    row_l = 68
    row_v = 92

    draw.text((col_x[0], row_l), "WiFi",         font=font_label, fill=(100, 100, 100))
    draw.text((col_x[0], row_v), "Optocam Zero", font=font_value, fill=(255, 255, 255))
    draw.text((col_x[1], row_l), "Password",     font=font_label, fill=(100, 100, 100))
    draw.text((col_x[1], row_v), "0026opto",     font=font_value, fill=(255, 255, 255))
    draw.text((col_x[2], row_l), "Browser",      font=font_label, fill=(100, 100, 100))
    draw.text((col_x[2], row_v), "192.168.4.1",  font=font_value, fill=(255, 255, 255))

    draw.line([(8, 148), (DISP_W - 8, 148)], fill=(40, 40, 40), width=1)

    hint   = "Hold center joystick to exit"
    hb     = draw.textbbox((0, 0), hint, font=font_hint)
    hint_h = hb[3] - hb[1]
    hint_y = 148 + (DISP_H - 148 - hint_h) // 2 - hb[1]
    draw.text(((DISP_W - (hb[2] - hb[0])) // 2, hint_y),
              hint, font=font_hint, fill=(60, 60, 60))

    display_image(img)


GALLERY_DIR = "/home/dkumkum/photos"

_capture_counter      = None
_capture_counter_lock = threading.Lock()


def get_next_capture_number():
    global _capture_counter
    with _capture_counter_lock:
        if _capture_counter is None:
            try:
                numbers = [
                    int(f[len("Optocamzero_"):-len(".jpg")])
                    for f in os.listdir(GALLERY_DIR)
                    if f.startswith("Optocamzero_") and f.endswith(".jpg")
                    and f[len("Optocamzero_"):-len(".jpg")].isdigit()
                ] if os.path.exists(GALLERY_DIR) else []
                _capture_counter = max(numbers) + 1 if numbers else 1
            except:
                _capture_counter = 1
        num = _capture_counter
        _capture_counter += 1
        return num


def get_gallery_images():
    try:
        if not os.path.exists(GALLERY_DIR):
            return []
        files = [
            os.path.join(GALLERY_DIR, f)
            for f in os.listdir(GALLERY_DIR)
            if f.startswith("Optocamzero_") and f.endswith(".jpg")
        ]
        files.sort(key=lambda f: int(os.path.basename(f)[len("Optocamzero_"):-len(".jpg")]))
        print(f"Found {len(files)} images")
        return files
    except Exception as e:
        print(f"Gallery scan error: {e}")
        return []


def display_gallery_image(filepath, index, total, confirm_delete=False):
    try:
        from PIL import ImageDraw, ImageFilter
        img = Image.open(filepath)
        img.draft("RGB", (DISP_W, DISP_H))
        img = img.convert("RGB")
        img = img.resize((DISP_W, DISP_H), Image.BILINEAR)

        font   = load_font(30)
        text   = f"{index}/{total}"
        draw   = ImageDraw.Draw(img)
        bbox_t = draw.textbbox((0, 0), text, font=font)
        text_h = bbox_t[3] - bbox_t[1]
        x, y   = 15, DISP_H - 15 - text_h

        shadow = Image.new("RGBA", (DISP_W, DISP_H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((x, y), text, font=font, fill=(0, 0, 0, 200))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))
        img    = img.convert("RGBA")
        img    = Image.alpha_composite(img, shadow)
        img    = img.convert("RGB")
        draw   = ImageDraw.Draw(img)
        draw.text((x, y), text, font=font, fill=(255, 255, 255))

        if confirm_delete:
            overlay = Image.new("RGBA", (DISP_W, DISP_H), (0, 0, 0, 160))
            img     = img.convert("RGBA")
            img     = Image.alpha_composite(img, overlay)
            img     = img.convert("RGB")
            draw    = ImageDraw.Draw(img)
            font_dialog = load_font(32)
            t1  = "Delete?"
            b1  = draw.textbbox((0, 0), t1, font=font_dialog)
            draw.text(((DISP_W - (b1[2]-b1[0])) // 2, 100), t1,
                      font=font_dialog, fill=(255, 255, 255))
            t2      = "YES: "
            b2      = draw.textbbox((0, 0), t2, font=font_dialog)
            text_w  = b2[2] - b2[0]
            text_h2 = b2[3] - b2[1]
            arrow_w = 16
            bx = (DISP_W - text_w - arrow_w) // 2
            by = 148
            draw.text((bx, by), t2, font=font_dialog, fill=(255, 255, 255))
            ax    = bx + text_w
            mid_y = by + (b2[1] + b2[3]) // 2
            draw.polygon(
                [(ax+8, mid_y-10), (ax, mid_y+8), (ax+16, mid_y+8)],
                fill=(255, 255, 255),
            )
            t3 = "NO: Any Button"
            b3 = draw.textbbox((0, 0), t3, font=font_dialog)
            draw.text(
                ((DISP_W - (b3[2]-b3[0])) // 2, by + text_h2 + 14),
                t3, font=font_dialog, fill=(180, 180, 180),
            )

        display_image(img)
    except Exception as e:
        print(f"Gallery load error: {e}")


def _save_image_async(captured_image, filepath, filename, film_name="No Filter", hdr_images=None):
    global saving_active
    try:
        start = time.time()
        if film_name == "HDR" and hdr_images is not None:
            print("Merging HDR exposures...")
            captured_image = _merge_hdr(hdr_images)
        else:
            captured_image = _apply_filter_by_name(captured_image, film_name)
        captured_image.save(filepath, "JPEG", quality=98, optimize=True)
        with open(filepath, "rb") as f:
            os.fsync(f.fileno())

        if not os.path.exists(filepath):
            print("File not created")
            return

        file_size = os.path.getsize(filepath)
        if file_size < 100000:
            print(f"File too small ({file_size} bytes) — deleting")
            try: os.remove(filepath)
            except: pass
            return

        try:
            test_img = Image.open(filepath)
            test_img.verify()
            test_img.close()
        except Exception as e:
            print(f"Corrupted: {e} — deleting")
            try: os.remove(filepath)
            except: pass
            return

        print(f"Saved {filename} ({file_size/1024/1024:.2f} MB) in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"Save error: {e}")
    finally:
        with _save_active_lock:
            saving_active -= 1


def capture_full_res(picam2):
    global capturing, camera_started, show_focus, config_cache, saving_active
    captured_image = None
    hdr_images     = None
    filepath       = None
    filename       = None
    is_hdr         = FILTERS[filter_index] == "HDR"

    try:
        with camera_lock:
            capturing  = True
            show_focus = True
            print("\n=== CAPTURE" + (" HDR" if is_hdr else "") + " ===")

            os.makedirs(GALLERY_DIR, exist_ok=True)
            number   = get_next_capture_number()
            filename = f"Optocamzero_{number}.jpg"
            filepath = os.path.join(GALLERY_DIR, filename)

            if camera_started:
                picam2.stop()
                camera_started = False
                time.sleep(0.05)

            if not is_hdr:
                picam2.configure(config_cache.capture_config)
                picam2.start()
                time.sleep(0.12)

                picam2.set_controls({"AfMode": 1, "AfTrigger": 0})
                focus_start = time.time()
                focused     = False
                while time.time() - focus_start < 1.0:
                    try:
                        metadata = picam2.capture_metadata()
                        af_state = metadata.get("AfState", 0)
                        if af_state == 2:
                            focused = True
                            print(f"Focus: {time.time()-focus_start:.2f}s")
                            break
                        elif af_state == 3:
                            break
                    except:
                        pass
                    time.sleep(0.03)
                if not focused:
                    print("AF timeout")
                show_focus = False

                for attempt in range(2):
                    try:
                        captured_image = picam2.capture_image()
                        if captured_image and captured_image.size[0] > 0:
                            print(f"Captured: {captured_image.size}")
                            break
                        captured_image = None
                        time.sleep(0.08)
                    except Exception as e:
                        print(f"Attempt {attempt+1}: {e}")
                        captured_image = None
                        time.sleep(0.08)

                time.sleep(0.05)
                picam2.stop()
                time.sleep(0.05)

                if captured_image is None:
                    print("Capture failed")
                    return None

                captured_image = captured_image.transpose(Image.ROTATE_90)

            else:
                picam2.configure(config_cache.capture_config)
                picam2.start()
                time.sleep(0.15)

                picam2.set_controls({"AfMode": 1, "AfTrigger": 0})
                focus_start = time.time()
                focused     = False
                while time.time() - focus_start < 1.0:
                    try:
                        metadata = picam2.capture_metadata()
                        if metadata.get("AfState", 0) == 2:
                            focused = True
                            break
                        elif metadata.get("AfState", 0) == 3:
                            break
                    except:
                        pass
                    time.sleep(0.03)
                if not focused:
                    print("AF timeout (HDR)")
                show_focus = False

                hdr_images = []
                exposure_times = [4000, 25000, 100000]
                for ev in exposure_times:
                    picam2.set_controls({"AeEnable": False, "ExposureTime": ev})
                    time.sleep(0.12)
                    try:
                        frame = picam2.capture_image()
                        if frame and frame.size[0] > 0:
                            hdr_images.append(frame.transpose(Image.ROTATE_90))
                            print(f"HDR frame {ev}us: {frame.size}")
                        else:
                            print(f"HDR frame {ev}us failed")
                    except Exception as e:
                        print(f"HDR frame {ev}us error: {e}")

                picam2.set_controls({"AeEnable": True})
                time.sleep(0.05)
                picam2.stop()
                time.sleep(0.05)

                if len(hdr_images) < 2:
                    print("Not enough HDR frames")
                    return None

                captured_image = hdr_images[len(hdr_images) // 2]

        print("Preview resuming...")

        with _save_active_lock:
            saving_active += 1
        threading.Thread(
            target=_save_image_async,
            args=(captured_image, filepath, filename, FILTERS[filter_index], hdr_images),
            daemon=True,
        ).start()

        return filepath

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback; traceback.print_exc()
        return None
    finally:
        capturing = False
        show_focus = False


def button_handler():
    global preview_active, capture_requested, exit_requested
    global gallery_active, gallery_index, gallery_images, gallery_needs_update
    global gallery_confirm_delete, gallery_empty_message_time
    global awb_mode_index, awb_mode_changed, awb_changed_time
    global no_space_message_time, splash_active
    global filter_index, filter_label_time, isp_changed
    global transfer_mode, transfer_screen_shown
    global _transfer_last_activity, _transfer_dimmed
    global _idle_last_activity, _idle_dimmed

    last_capture     = 0
    last_preview     = 0
    last_joy_up      = 0
    last_joy_down    = 0
    debounce         = 0.3
    left_held_since  = 0
    right_held_since = 0
    last_scroll_time = 0
    HOLD_THRESHOLD   = 0.5
    FAST_INTERVAL    = 0.15

    joy_press_times      = []
    TRIPLE_PRESS_WINDOW  = 0.8
    joy_press_was_down   = False
    joy_press_down_time  = 0
    joy_long_press_fired = False

    print("Buttons ready")

    while not exit_requested:
        try:
            now = time.time()

            if splash_active:
                any_pressed = (
                    not GPIO.input(BUTTON_CAPTURE) or not GPIO.input(BUTTON_PREVIEW) or
                    not GPIO.input(JOYSTICK_UP)    or not GPIO.input(JOYSTICK_DOWN)  or
                    not GPIO.input(JOYSTICK_LEFT)  or not GPIO.input(JOYSTICK_RIGHT) or
                    not GPIO.input(JOYSTICK_PRESS)
                )
                if any_pressed:
                    splash_active = False
                    _idle_last_activity = time.time()
                    joy_press_times.clear()
                    print("Splash closed")
                time.sleep(0.05)
                continue

            if not transfer_mode:
                any_input = (
                    not GPIO.input(BUTTON_CAPTURE) or not GPIO.input(BUTTON_PREVIEW) or
                    not GPIO.input(JOYSTICK_UP)    or not GPIO.input(JOYSTICK_DOWN)  or
                    not GPIO.input(JOYSTICK_LEFT)  or not GPIO.input(JOYSTICK_RIGHT) or
                    not GPIO.input(JOYSTICK_PRESS)
                )
                if any_input:
                    _idle_last_activity = time.time()
                    if _idle_dimmed:
                        _idle_dimmed = False
                        set_backlight(True)
                        time.sleep(0.3)
                        continue

            if not GPIO.input(JOYSTICK_UP):
                if now - last_joy_up > debounce:
                    last_joy_up = now
                    if gallery_active and gallery_images:
                        if gallery_confirm_delete:
                            filepath = gallery_images[gallery_index]
                            try:
                                os.remove(filepath)
                                print(f"Deleted: {os.path.basename(filepath)}")
                                thumb_dir = os.path.join(GALLERY_DIR, ".thumbs")
                                if os.path.exists(thumb_dir):
                                    for f in os.listdir(thumb_dir):
                                        if f.startswith(os.path.basename(filepath) + "_"):
                                            try: os.remove(os.path.join(thumb_dir, f))
                                            except: pass
                            except Exception as e:
                                print(f"Delete error: {e}")
                            gallery_images.pop(gallery_index)
                            gallery_confirm_delete = False
                            if not gallery_images:
                                gallery_active = False
                                preview_active = True
                            else:
                                gallery_index = min(gallery_index, len(gallery_images) - 1)
                                gallery_needs_update = True
                        else:
                            gallery_confirm_delete = True
                            gallery_needs_update   = True
                    elif preview_active and not capturing:
                        filter_index      = (filter_index - 1) % len(FILTERS)
                        filter_label_time = now
                        isp_changed       = True
                        print(f"Filter: {FILTERS[filter_index]}")

            if not GPIO.input(JOYSTICK_DOWN):
                if now - last_joy_down > debounce:
                    last_joy_down = now
                    if gallery_active and gallery_confirm_delete:
                        gallery_confirm_delete = False
                        gallery_needs_update   = True
                    elif preview_active and not capturing:
                        filter_index      = (filter_index + 1) % len(FILTERS)
                        filter_label_time = now
                        isp_changed       = True
                        print(f"Filter: {FILTERS[filter_index]}")

            joy_is_down = not GPIO.input(JOYSTICK_PRESS)

            if joy_is_down and not joy_press_was_down:
                joy_press_down_time  = now
                joy_long_press_fired = False
                joy_press_was_down   = True

            elif joy_is_down and joy_press_was_down:
                if not joy_long_press_fired and now - joy_press_down_time >= 1.5:
                    joy_long_press_fired = True
                    joy_press_times.clear()
                    transfer_mode = not transfer_mode
                    transfer_screen_shown = False
                    if transfer_mode:
                        gallery_active = False
                        splash_active  = False
                        preview_active = False
                        print("Transfer mode ON")
                        _transfer_last_activity = time.time()
                        _transfer_dimmed        = False
                        subprocess.Popen([
                            "systemctl", "start",
                            "optocam-hotspot.service", "optocam-gallery.service",
                        ])
                    else:
                        preview_active = True
                        print("Transfer mode OFF")
                        if _transfer_dimmed:
                            set_backlight(True)
                        _transfer_dimmed    = False
                        _idle_last_activity = time.time()
                        _idle_dimmed        = False
                        subprocess.Popen([
                            "systemctl", "stop",
                            "optocam-gallery.service", "optocam-hotspot.service",
                        ])

            elif not joy_is_down and joy_press_was_down:
                joy_press_was_down = False
                if not joy_long_press_fired and now - joy_press_down_time > 0.02:
                    joy_press_times.append(now)
                    joy_press_times[:] = [t for t in joy_press_times if now - t < TRIPLE_PRESS_WINDOW]
                    if len(joy_press_times) >= 3:
                        joy_press_times.clear()
                        gallery_active        = False
                        gallery_confirm_delete = False
                        transfer_mode         = False
                        splash_active         = True
                        print("Splash activated")
                    elif transfer_mode:
                        pass
                    elif gallery_active:
                        if gallery_confirm_delete:
                            gallery_confirm_delete = False
                            gallery_needs_update   = True
                        else:
                            gallery_active = False
                            preview_active = True
                            print("Gallery closed")
                    else:
                        gallery_images = get_gallery_images()
                        if gallery_images:
                            gallery_index        = len(gallery_images) - 1
                            gallery_active       = True
                            preview_active       = False
                            gallery_needs_update = True
                            print(f"Gallery opened ({len(gallery_images)} images)")
                        else:
                            gallery_empty_message_time = time.time()
                            print("Gallery empty")

            if transfer_mode:
                time.sleep(0.05)
                continue

            if not GPIO.input(BUTTON_CAPTURE):
                if now - last_capture > debounce:
                    last_capture = now
                    if gallery_active:
                        if gallery_confirm_delete:
                            gallery_confirm_delete = False
                            gallery_needs_update   = True
                        else:
                            gallery_active = False
                            preview_active = True
                            print("Gallery closed")
                    elif preview_active and not capturing:
                        try:
                            check_path = GALLERY_DIR if os.path.exists(GALLERY_DIR) else os.path.dirname(GALLERY_DIR)
                            stat       = os.statvfs(check_path)
                            free_bytes = stat.f_bavail * stat.f_bsize
                            if free_bytes < 20 * 1024 * 1024:
                                no_space_message_time = time.time()
                                print("No space in card")
                            else:
                                capture_requested = True
                                print("CAPTURE")
                        except:
                            capture_requested = True
                            print("CAPTURE")

            if not GPIO.input(BUTTON_PREVIEW):
                if now - last_preview > debounce:
                    if gallery_active and gallery_confirm_delete:
                        gallery_confirm_delete = False
                        gallery_needs_update   = True
                        last_preview = now
                    elif not gallery_active:
                        preview_active = not preview_active
                        last_preview   = now
                        print(f"Preview {'ON' if preview_active else 'OFF'}")

            if gallery_active and gallery_images:
                left_pressed  = not GPIO.input(JOYSTICK_LEFT)
                right_pressed = not GPIO.input(JOYSTICK_RIGHT)

                if left_pressed:
                    if gallery_confirm_delete:
                        gallery_confirm_delete = False
                        gallery_needs_update   = True
                        left_held_since = now
                    elif left_held_since == 0:
                        left_held_since = now
                        gallery_index = (gallery_index - 1) % len(gallery_images)
                        gallery_needs_update = True
                        last_scroll_time = now
                    elif now - left_held_since > HOLD_THRESHOLD:
                        if now - last_scroll_time > FAST_INTERVAL:
                            gallery_index = (gallery_index - 1) % len(gallery_images)
                            gallery_needs_update = True
                            last_scroll_time = now
                else:
                    left_held_since = 0

                if right_pressed:
                    if gallery_confirm_delete:
                        gallery_confirm_delete = False
                        gallery_needs_update   = True
                        right_held_since = now
                    elif right_held_since == 0:
                        right_held_since = now
                        gallery_index = (gallery_index + 1) % len(gallery_images)
                        gallery_needs_update = True
                        last_scroll_time = now
                    elif now - right_held_since > HOLD_THRESHOLD:
                        if now - last_scroll_time > FAST_INTERVAL:
                            gallery_index = (gallery_index + 1) % len(gallery_images)
                            gallery_needs_update = True
                            last_scroll_time = now
                else:
                    right_held_since = 0

            elif preview_active and not capturing:
                if not GPIO.input(JOYSTICK_LEFT):
                    if now - last_scroll_time > debounce:
                        last_scroll_time = now
                        awb_mode_index   = (awb_mode_index - 1) % len(AWB_MODES)
                        awb_mode_changed = True
                        awb_changed_time = now
                        print(f"AWB: {AWB_MODES[awb_mode_index][1]}")
                if not GPIO.input(JOYSTICK_RIGHT):
                    if now - last_scroll_time > debounce:
                        last_scroll_time = now
                        awb_mode_index   = (awb_mode_index + 1) % len(AWB_MODES)
                        awb_mode_changed = True
                        awb_changed_time = now
                        print(f"AWB: {AWB_MODES[awb_mode_index][1]}")

            time.sleep(0.02)

        except Exception as e:
            print(f"Button error: {e}")
            time.sleep(0.1)


preview_active             = True
capture_requested          = False
exit_requested             = False
camera_started             = False
capturing                  = False
show_focus                 = False
capture_dot_time           = 0
gallery_active             = False
gallery_index              = 0
gallery_images             = []
gallery_needs_update       = False
gallery_confirm_delete     = False
gallery_empty_message_time = 0
no_space_message_time      = 0
splash_active              = False
awb_mode_index             = AWB_MODES.index(next(m for m in AWB_MODES if m[1] == "Daylight"))
awb_mode_changed           = False
awb_changed_time           = 0
filter_index               = FILTERS.index("Film Standard")
saving_active              = 0
_save_active_lock          = threading.Lock()
filter_label_time          = 0
isp_changed                = False
transfer_mode              = False
transfer_screen_shown      = False
_transfer_last_refresh     = 0
_transfer_last_activity    = 0.0
_transfer_dimmed           = False
_idle_last_activity        = 0.0
_idle_dimmed               = False
IDLE_DIM_TIMEOUT           = 90.0


def main():
    log("main() called")
    global preview_active, capture_requested, exit_requested, camera_started
    global capturing, show_focus, capture_dot_time, config_cache
    global gallery_active, gallery_index, gallery_images, gallery_needs_update
    global gallery_confirm_delete, gallery_empty_message_time
    global awb_mode_index, awb_mode_changed, awb_changed_time
    global no_space_message_time, splash_active
    global filter_index, filter_label_time, isp_changed, saving_active
    global transfer_mode, transfer_screen_shown
    global _transfer_last_refresh, _transfer_last_activity, _transfer_dimmed
    global _idle_last_activity, _idle_dimmed

    gc.disable()

    systemd_notify("STATUS=Starting SPI display")
    log("Initializing display...")
    init_display()
    set_backlight(True)
    show_splash()
    systemd_notify("STATUS=SPI display ready, starting camera")
    log("Display ready")

    log("Importing Picamera2...")
    from picamera2 import Picamera2
    log("Picamera2 imported")

    systemd_notify("STATUS=Opening camera")
    picam2 = Picamera2()
    clear_display()
    config_cache = CameraConfigCache(picam2)

    print("Optocam Zero — ILI9486 480x320 | HDR mode available")
    print(f"Shutter GPIO {BUTTON_CAPTURE} | Preview GPIO {BUTTON_PREVIEW} | Joystick for filters/AWB/gallery")

    button_thread = threading.Thread(target=button_handler, daemon=True)
    button_thread.start()

    frame_count     = 0
    last_fps_report = time.time()
    systemd_ready_sent = False
    _idle_last_activity = time.time()

    try:
        while not exit_requested:

            if not transfer_mode and not splash_active:
                if not _idle_dimmed and time.time() - _idle_last_activity > IDLE_DIM_TIMEOUT:
                    _idle_dimmed = True
                    set_backlight_brightness(8)

            if splash_active:
                if camera_started:
                    with camera_lock:
                        if camera_started:
                            picam2.stop()
                            camera_started   = False
                            capture_dot_time = 0
                set_backlight(True)
                show_splash()
                time.sleep(0.05)

            elif transfer_mode:
                if camera_started:
                    with camera_lock:
                        if camera_started:
                            picam2.stop()
                            camera_started   = False
                            capture_dot_time = 0
                if not transfer_screen_shown:
                    transfer_screen_shown   = True
                    _transfer_last_refresh  = 0
                    _transfer_last_activity = time.time()
                    _transfer_dimmed        = False
                    set_backlight(True)
                any_pressed = (
                    not GPIO.input(BUTTON_CAPTURE) or not GPIO.input(BUTTON_PREVIEW) or
                    not GPIO.input(JOYSTICK_UP)    or not GPIO.input(JOYSTICK_DOWN)  or
                    not GPIO.input(JOYSTICK_LEFT)  or not GPIO.input(JOYSTICK_RIGHT) or
                    not GPIO.input(JOYSTICK_PRESS)
                )
                if any_pressed:
                    if _transfer_dimmed:
                        _transfer_dimmed       = False
                        _transfer_last_refresh = 0
                        set_backlight(True)
                    _transfer_last_activity = time.time()
                elif not _transfer_dimmed and time.time() - _transfer_last_activity > 30:
                    _transfer_dimmed = True
                    set_backlight_brightness(8)
                if time.time() - _transfer_last_refresh >= 0.5:
                    _transfer_last_refresh = time.time()
                    show_transfer_mode_screen()
                time.sleep(0.1)

            elif gallery_active:
                if camera_started:
                    with camera_lock:
                        if camera_started:
                            picam2.stop()
                            camera_started   = False
                            capture_dot_time = 0
                if gallery_needs_update and gallery_images:
                    gallery_needs_update = False
                    idx   = gallery_index
                    total = len(gallery_images)
                    set_backlight(True)
                    display_gallery_image(gallery_images[idx], idx+1, total, gallery_confirm_delete)
                    print(f"Gallery: {idx+1}/{total}")
                time.sleep(0.02)

            elif preview_active and not capturing:
                if not camera_started:
                    with camera_lock:
                        set_backlight(True)
                        picam2.configure(config_cache.preview_config)
                        picam2.start()
                        camera_started      = True
                        _idle_last_activity = time.time()
                        if not systemd_ready_sent:
                            systemd_notify("READY=1", "STATUS=Preview started")
                            systemd_ready_sent = True
                        print("Preview started")

                if capture_requested:
                    capture_requested = False
                    capture_dot_time  = time.time()
                    threading.Thread(
                        target=capture_full_res,
                        args=(picam2,),
                        daemon=True,
                    ).start()

                if camera_started and not capturing:
                    try:
                        with camera_lock:
                            if camera_started and not capturing:
                                if awb_mode_changed:
                                    awb_mode_changed = False
                                    picam2.set_controls({"AwbMode": AWB_MODES[awb_mode_index][0]})
                                if isp_changed:
                                    isp_changed = False
                                    picam2.set_controls(
                                        _FILM_ISP.get(FILTERS[filter_index], _FILM_ISP["No Filter"])
                                    )

                                req          = picam2.capture_request()
                                preview_image = req.make_image("main")
                                metadata     = req.get_metadata()
                                req.release()

                                preview_image = preview_image.transpose(Image.ROTATE_90)
                                preview_image = apply_filter(preview_image)

                                if preview_image.size != (DISP_W, DISP_H):
                                    preview_image = preview_image.resize(
                                        (DISP_W, DISP_H), Image.LANCZOS
                                    )

                                if capture_dot_time > 0 and (time.time() - capture_dot_time) >= 2.0:
                                    capture_dot_time = 0

                                from PIL import ImageDraw
                                font_hud      = load_font(28)
                                font_awb_full = load_font(28)
                                font_awb_abbr = load_font(26)
                                tmp_draw      = ImageDraw.Draw(preview_image)

                                iso_val     = str(nearest_standard_iso(metadata.get("AnalogueGain", 1.0)))
                                exp         = metadata.get("ExposureTime", 10000)
                                shutter_val = nearest_standard_shutter(exp) if exp > 0 else "?"
                                awb_switching = time.time() - awb_changed_time < 1.0
                                awb_label   = (AWB_MODES[awb_mode_index][1] if awb_switching
                                               else AWB_MODES[awb_mode_index][2])
                                font_awb    = font_awb_full if awb_switching else font_awb_abbr

                                b_awb = tmp_draw.textbbox((0, 0), awb_label, font=font_awb)
                                ax    = 15
                                ay    = 15 - b_awb[1]

                                b_iso = tmp_draw.textbbox((0, 0), iso_val, font=font_hud)
                                ix    = 15

                                b_sh  = tmp_draw.textbbox((0, 0), shutter_val, font=font_hud)
                                sx    = DISP_W - 15 - (b_sh[2] - b_sh[0])

                                hud_bottom_y = DISP_H - 15 - max(b_iso[3]-b_iso[1], b_sh[3]-b_sh[1])
                                iy = hud_bottom_y
                                sy = hud_bottom_y

                                awb_shadow = get_cached_shadow("awb",     awb_label,   ax, ay, font_awb)
                                iso_shadow = get_cached_shadow("iso",     iso_val,     ix, iy, font_hud)
                                sh_shadow  = get_cached_shadow("shutter", shutter_val, sx, sy, font_hud)

                                overlay = Image.alpha_composite(awb_shadow, iso_shadow)
                                overlay = Image.alpha_composite(overlay, sh_shadow)

                                centre_msg     = None
                                centre_msg_key = None
                                if gallery_empty_message_time > 0 and time.time() - gallery_empty_message_time < 1.0:
                                    centre_msg     = "No image in card"
                                    centre_msg_key = "empty_msg"
                                elif gallery_empty_message_time > 0:
                                    gallery_empty_message_time = 0

                                if centre_msg is None and no_space_message_time > 0 and time.time() - no_space_message_time < 1.0:
                                    centre_msg     = "No space in card"
                                    centre_msg_key = "no_space_msg"
                                elif centre_msg is None and no_space_message_time > 0:
                                    no_space_message_time = 0

                                if (centre_msg is None and filter_label_time > 0
                                        and time.time() - filter_label_time < 1.5
                                        and gallery_empty_message_time == 0
                                        and no_space_message_time == 0):
                                    centre_msg     = FILTERS[filter_index]
                                    centre_msg_key = "filter_label"
                                elif centre_msg is None and filter_label_time > 0:
                                    filter_label_time = 0

                                if centre_msg is not None:
                                    font_msg = load_font(28)
                                    _tmp     = ImageDraw.Draw(preview_image)
                                    bbox     = _tmp.textbbox((0, 0), centre_msg, font=font_msg)
                                    mx = (DISP_W - (bbox[2]-bbox[0])) // 2
                                    my = (DISP_H - (bbox[3]-bbox[1])) // 2
                                    msg_shadow = get_cached_shadow(centre_msg_key, centre_msg, mx, my, font_msg)
                                    overlay    = Image.alpha_composite(overlay, msg_shadow)

                                indicator = get_filter_indicator(FILTERS[filter_index])
                                overlay   = Image.alpha_composite(overlay, indicator)

                                preview_image = preview_image.convert("RGBA")
                                preview_image = Image.alpha_composite(preview_image, overlay)
                                preview_image = preview_image.convert("RGB")

                                draw_hud = ImageDraw.Draw(preview_image)
                                draw_hud.text((ax, ay), awb_label,   font=font_awb, fill=(255, 255, 255))
                                draw_hud.text((ix, iy), iso_val,     font=font_hud, fill=(255, 255, 255))
                                draw_hud.text((sx, sy), shutter_val, font=font_hud, fill=(255, 255, 255))

                                if centre_msg is not None:
                                    draw_hud.text((mx, my), centre_msg, font=font_msg, fill=(255, 255, 255))

                                if saving_active > 0:
                                    sp_r   = 8
                                    sp_cx  = DISP_W // 2
                                    sp_cy  = hud_bottom_y + (b_iso[1] + b_iso[3]) // 2
                                    sp_a   = int(time.time() * 360) % 360
                                    sp_box   = [sp_cx-sp_r,   sp_cy-sp_r,   sp_cx+sp_r,   sp_cy+sp_r]
                                    sp_box_s = [sp_cx-sp_r+1, sp_cy-sp_r+1, sp_cx+sp_r+1, sp_cy+sp_r+1]
                                    draw_hud.arc(sp_box_s, start=sp_a, end=sp_a+270, fill=(0, 0, 0),       width=2)
                                    draw_hud.arc(sp_box,   start=sp_a, end=sp_a+270, fill=(255, 255, 255), width=2)

                                display_image(preview_image)
                                frame_count += 1
                                if time.time() - last_fps_report >= 5.0:
                                    elapsed = time.time() - last_fps_report
                                    print(f"{frame_count / elapsed:.1f} fps")
                                    frame_count     = 0
                                    last_fps_report = time.time()

                    except Exception as e:
                        print(f"Preview error: {e}")
                        time.sleep(0.1)

                time.sleep(0.001)

            else:
                if camera_started and not capturing:
                    with camera_lock:
                        if camera_started:
                            picam2.stop()
                            camera_started   = False
                            capture_dot_time = 0
                            set_backlight(False)
                            clear_display()
                            print("Preview stopped")
                time.sleep(0.05)

    except KeyboardInterrupt:
        print("Shutting down...")
        exit_requested = True

    finally:
        with camera_lock:
            if camera_started:
                picam2.stop()
        set_backlight(False)
        _backlight_pwm.stop()
        GPIO.cleanup()
        spi.close()
        gc.enable()
        print("Shutdown complete")


if __name__ == "__main__":
    main()
