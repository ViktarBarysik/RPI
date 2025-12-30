#!/usr/bin/env python3
import os
import time
import json
import socket
import shutil
import subprocess
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont

# ---- Settings ----
FB_DEV = "/dev/fb0"
CACHE_WEATHER = "/tmp/batumi_weather_cache.json"
CACHE_PUBLIC_IP = "/tmp/public_ip_cache.json"

BATUMI_LAT = 41.6168
BATUMI_LON = 41.6367

REFRESH_EVERY_SEC = 10          # screen redraw
WEATHER_REFRESH_SEC = 10 * 60   # weather cache
PUBLIC_IP_REFRESH_SEC = 10 * 60 # public IP cache

WEATHER_CODE = {
    0: ("Clear", "sun"),
    1: ("Mainly clear", "sun"),
    2: ("Partly cloudy", "cloud_sun"),
    3: ("Overcast", "cloud"),
    45: ("Fog", "cloud"),
    48: ("Fog", "cloud"),
    51: ("Drizzle", "rain"),
    53: ("Drizzle", "rain"),
    55: ("Drizzle", "rain"),
    56: ("Freezing drizzle", "rain"),
    57: ("Freezing drizzle", "rain"),
    61: ("Rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"),
    67: ("Freezing rain", "rain"),
    71: ("Snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Showers", "rain"),
    81: ("Showers", "rain"),
    82: ("Showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "thunder"),
    96: ("Thunderstorm hail", "thunder"),
    99: ("Thunderstorm hail", "thunder"),
}

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

SESSION = requests.Session()


# ---- Framebuffer utils ----
def fb_info():
    """
    Returns (W,H,BPP).
    We assume 16bpp (RGB565) if cannot detect.
    """
    W, H, BPP = 480, 320, 16
    try:
        out = subprocess.check_output(["fbset", "-fb", FB_DEV, "-s"], text=True, timeout=2)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("geometry"):
                # geometry 320 480 320 480 16
                p = line.split()
                W = int(p[1]); H = int(p[2])
                if len(p) >= 6:
                    BPP = int(p[5])
                break
    except Exception:
        pass
    if BPP not in (16, 32):
        BPP = 16
    return W, H, BPP


def rgb888_to_rgb565_bytes(img_rgb):
    """
    Convert PIL RGB image to RGB565 little-endian bytes.
    """
    # img_rgb: mode "RGB"
    raw = img_rgb.tobytes()  # R,G,B...
    out = bytearray((len(raw) // 3) * 2)
    j = 0
    for i in range(0, len(raw), 3):
        r = raw[i]
        g = raw[i + 1]
        b = raw[i + 2]
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j] = rgb565 & 0xFF
        out[j + 1] = (rgb565 >> 8) & 0xFF
        j += 2
    return out


def write_to_fb(img, W, H, BPP):
    """
    Writes image to framebuffer. Supports 16bpp (RGB565) and 32bpp (XRGB8888-ish).
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    if img.size != (W, H):
        img = img.resize((W, H), Image.BILINEAR)

    try:
        with open(FB_DEV, "wb", buffering=0) as fb:
            if BPP == 16:
                fb.write(rgb888_to_rgb565_bytes(img))
            else:
                # 32bpp fallback: write BGRA (approx) - some fbs expect XRGB.
                # We'll write B,G,R,0 to be safe-ish.
                raw = img.tobytes()
                out = bytearray((len(raw) // 3) * 4)
                j = 0
                for i in range(0, len(raw), 3):
                    r = raw[i]; g = raw[i+1]; b = raw[i+2]
                    out[j] = b
                    out[j+1] = g
                    out[j+2] = r
                    out[j+3] = 0
                    j += 4
                fb.write(out)
    except Exception:
        # If writing fails, do nothing (service will keep running).
        pass


# ---- Data helpers ----
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "N/A"


def get_default_iface():
    try:
        out = subprocess.check_output(["bash", "-lc", "ip route get 1.1.1.1 | head -1"], text=True).strip()
        parts = out.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    except Exception:
        pass
    return "N/A"


def iface_is_up(iface: str) -> bool:
    try:
        path = f"/sys/class/net/{iface}/operstate"
        if os.path.exists(path):
            return open(path, "r").read().strip() == "up"
    except Exception:
        pass
    return False


def get_public_ip_cached():
    # cache hit?
    try:
        if os.path.exists(CACHE_PUBLIC_IP):
            c = json.load(open(CACHE_PUBLIC_IP, "r"))
            if int(time.time()) - int(c.get("ts", 0)) < PUBLIC_IP_REFRESH_SEC:
                return c.get("ip", "N/A")
    except Exception:
        pass

    # fetch
    ip = "N/A"
    try:
        ip = SESSION.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        pass

    # save cache
    try:
        json.dump({"ts": int(time.time()), "ip": ip}, open(CACHE_PUBLIC_IP, "w"))
    except Exception:
        pass
    return ip


def read_cpu_temp_c():
    try:
        t = int(open("/sys/class/thermal/thermal_zone0/temp", "r").read().strip())
        return t / 1000.0
    except Exception:
        return None


def read_load():
    try:
        return os.getloadavg()
    except Exception:
        return None


def read_mem():
    """returns (used_mb, total_mb)"""
    try:
        meminfo = open("/proc/meminfo", "r").read().splitlines()
        m = {}
        for line in meminfo:
            k, v = line.split(":", 1)
            m[k.strip()] = int(v.strip().split()[0])  # kB
        total = m.get("MemTotal", 0) / 1024
        avail = m.get("MemAvailable", 0) / 1024
        used = total - avail
        return used, total
    except Exception:
        return None


def read_disk_root():
    """returns (used_gb, total_gb)"""
    try:
        du = shutil.disk_usage("/")
        used = du.used / (1024**3)
        total = du.total / (1024**3)
        return used, total
    except Exception:
        return None


def fetch_batumi_weather():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": BATUMI_LAT,
        "longitude": BATUMI_LON,
        "current_weather": True,
        "timezone": "auto",
    }
    r = SESSION.get(url, params=params, timeout=6)
    r.raise_for_status()
    data = r.json()
    cw = data.get("current_weather", {})
    temp = cw.get("temperature")
    wind = cw.get("windspeed")
    code = cw.get("weathercode")
    desc, icon = WEATHER_CODE.get(code, (f"Code {code}", "cloud"))
    return {"ts": int(time.time()), "temp": temp, "wind": wind, "code": code, "desc": desc, "icon": icon}


def get_weather_cached():
    try:
        if os.path.exists(CACHE_WEATHER):
            c = json.load(open(CACHE_WEATHER, "r"))
            if int(time.time()) - int(c.get("ts", 0)) < WEATHER_REFRESH_SEC:
                return c
    except Exception:
        pass

    try:
        w = fetch_batumi_weather()
        json.dump(w, open(CACHE_WEATHER, "w"))
        return w
    except Exception:
        try:
            if os.path.exists(CACHE_WEATHER):
                return json.load(open(CACHE_WEATHER, "r"))
        except Exception:
            pass
        return {"temp": None, "wind": None, "code": None, "desc": "N/A", "icon": "cloud"}


def clamp(s: str, maxlen: int):
    s = s if s is not None else ""
    return (s[:maxlen - 1] + "…") if len(s) > maxlen else s


def draw_bar(draw, x, y, w, h, frac):
    draw.rectangle([x, y, x + w, y + h], outline="gray", width=2)
    frac = max(0.0, min(1.0, frac))
    draw.rectangle([x + 2, y + 2, x + 2 + int((w - 4) * frac), y + h - 2], fill="white")


# ---- Weather icons (vector-ish) ----
def icon_sun(d, x, y, s):
    r = s // 3
    d.ellipse([x + r, y + r, x + s - r, y + s - r], outline="yellow", width=4)
    cx, cy = x + s // 2, y + s // 2
    for dx, dy in [(0, -1), (1, 0), (0, 1), (-1, 0), (1, -1), (1, 1), (-1, 1), (-1, -1)]:
        d.line([cx, cy, cx + dx * (s // 2), cy + dy * (s // 2)], fill="yellow", width=3)


def icon_cloud(d, x, y, s):
    d.ellipse([x + s * 0.05, y + s * 0.40, x + s * 0.45, y + s * 0.85], outline="white", width=3)
    d.ellipse([x + s * 0.25, y + s * 0.20, x + s * 0.70, y + s * 0.80], outline="white", width=3)
    d.ellipse([x + s * 0.55, y + s * 0.40, x + s * 0.95, y + s * 0.85], outline="white", width=3)
    d.rectangle([x + s * 0.12, y + s * 0.60, x + s * 0.88, y + s * 0.88], outline="white", width=3)


def icon_rain(d, x, y, s):
    icon_cloud(d, x, y, s)
    for i in range(4):
        xx = x + int(s * (0.22 + i * 0.18))
        d.line([xx, y + int(s * 0.88), xx - 8, y + s + 12], fill="cyan", width=3)


def icon_thunder(d, x, y, s):
    icon_cloud(d, x, y, s)
    bolt = [
        (x + int(s * 0.55), y + int(s * 0.85)),
        (x + int(s * 0.45), y + int(s * 1.05)),
        (x + int(s * 0.58), y + int(s * 1.05)),
        (x + int(s * 0.42), y + int(s * 1.28)),
    ]
    d.line(bolt, fill="yellow", width=4)


def icon_snow(d, x, y, s):
    icon_cloud(d, x, y, s)
    for i in range(3):
        cx = x + int(s * (0.30 + i * 0.22))
        cy = y + int(s * 1.05)
        d.line([cx - 10, cy, cx + 10, cy], fill="white", width=2)
        d.line([cx, cy - 10, cx, cy + 10], fill="white", width=2)
        d.line([cx - 7, cy - 7, cx + 7, cy + 7], fill="white", width=2)
        d.line([cx - 7, cy + 7, cx + 7, cy - 7], fill="white", width=2)


def draw_weather_icon(d, kind, x, y, s):
    if kind == "sun":
        icon_sun(d, x, y, s)
    elif kind == "cloud_sun":
        icon_sun(d, x, y, s)
        icon_cloud(d, x + int(s * 0.20), y + int(s * 0.25), s)
    elif kind == "rain":
        icon_rain(d, x, y, s)
    elif kind == "thunder":
        icon_thunder(d, x, y, s)
    elif kind == "snow":
        icon_snow(d, x, y, s)
    else:
        icon_cloud(d, x, y, s)


def build_fonts(W, H):
    pad = 12
    if W >= 480 and H <= 320:
        title_sz, med_sz, sm_sz = 34, 22, 18
        clock_sz, date_sz = 58, 18
        icon_size = 125
        desc_max = 20
    else:
        title_sz, med_sz, sm_sz = 34, 22, 18
        clock_sz, date_sz = 56, 18
        icon_size = 135
        desc_max = 18

    fonts = {
        "pad": pad,
        "title_sz": title_sz,
        "med_sz": med_sz,
        "sm_sz": sm_sz,
        "clock_sz": clock_sz,
        "date_sz": date_sz,
        "icon_size": icon_size,
        "desc_max": desc_max,
        "FONT_TITLE": ImageFont.truetype(FONT_BOLD, title_sz),
        "FONT_MED": ImageFont.truetype(FONT_REG, med_sz),
        "FONT_SM": ImageFont.truetype(FONT_REG, sm_sz),
        "FONT_CLOCK": ImageFont.truetype(FONT_BOLD, clock_sz),
        "FONT_DATE": ImageFont.truetype(FONT_REG, date_sz),
    }
    return fonts


def render_loop():
    W, H, BPP = fb_info()
    cfg = build_fonts(W, H)

    # reuse one image buffer to reduce allocations
    img = Image.new("RGB", (W, H), "black")
    d = ImageDraw.Draw(img)

    pad = cfg["pad"]

    while True:
        t0 = time.time()

        # data
        clock = datetime.now().strftime("%H:%M")
        date_line = datetime.now().strftime("%d %b %Y")
        local_ip = get_local_ip()
        public_ip = get_public_ip_cached()
        iface = get_default_iface()
        link = "UP" if iface != "N/A" and iface_is_up(iface) else "DOWN"
        cpu_t = read_cpu_temp_c()
        load = read_load()
        mem = read_mem()
        disk = read_disk_root()
        weather = get_weather_cached()

        # clear background
        d.rectangle([0, 0, W, H], fill="black")

        # header left
        d.text((pad, pad), "Batumi", font=cfg["FONT_TITLE"], fill="white")

        # big clock top-right
        clock_w = d.textlength(clock, font=cfg["FONT_CLOCK"])
        d.text((W - pad - clock_w, pad - 2), clock, font=cfg["FONT_CLOCK"], fill="white")

        # date under clock
        date_w = d.textlength(date_line, font=cfg["FONT_DATE"])
        d.text((W - pad - date_w, pad + cfg["clock_sz"] - 6), date_line, font=cfg["FONT_DATE"], fill="gray")

        # weather block
        y_weather = pad + cfg["clock_sz"] + 18
        icon_size = cfg["icon_size"]
        icon_x = W - icon_size - pad
        icon_y = y_weather - 10
        draw_weather_icon(d, weather.get("icon", "cloud"), icon_x, icon_y, icon_size)

        desc = clamp(weather.get("desc", "N/A"), cfg["desc_max"])
        temp = weather.get("temp")
        wind = weather.get("wind")
        temp_str = f"{temp:.1f}°C" if isinstance(temp, (int, float)) else "N/A"
        wind_str = f"{wind:.0f} km/h" if isinstance(wind, (int, float)) else "N/A"

        d.text((pad, y_weather), f"Weather: {desc}", font=cfg["FONT_MED"], fill="cyan")
        d.text((pad, y_weather + cfg["med_sz"] + 6), f"Temp: {temp_str}", font=cfg["FONT_MED"], fill="cyan")
        d.text((pad, y_weather + (cfg["med_sz"] + 6) * 2), f"Wind: {wind_str}", font=cfg["FONT_MED"], fill="cyan")

        # network block
        y_net = y_weather + (cfg["med_sz"] + 6) * 3 + 10
        d.text((pad, y_net), f"IF: {iface} ({link})", font=cfg["FONT_SM"], fill="white")
        d.text((pad, y_net + cfg["sm_sz"] + 6), f"LAN: {clamp(local_ip, 28)}", font=cfg["FONT_SM"], fill="white")
        d.text((pad, y_net + (cfg["sm_sz"] + 6) * 2), f"WAN: {clamp(public_ip, 28)}", font=cfg["FONT_SM"], fill="white")

        # system
        y_sys = y_net + (cfg["sm_sz"] + 6) * 3 + 8
        cpu_str = f"{cpu_t:.1f}°C" if cpu_t is not None else "N/A"
        d.text((pad, y_sys), f"CPU: {cpu_str}", font=cfg["FONT_SM"], fill="white")
        if load is not None:
            d.text((pad + 160, y_sys), f"Load: {load[0]:.2f}", font=cfg["FONT_SM"], fill="white")

        y_sys2 = y_sys + cfg["sm_sz"] + 8
        bar_x = pad + 175
        bar_w = max(80, W - bar_x - pad)

        if mem is not None:
            used, total = mem
            frac = used / total if total > 0 else 0
            d.text((pad, y_sys2), f"RAM: {used:.0f}/{total:.0f}MB", font=cfg["FONT_SM"], fill="white")
            draw_bar(d, bar_x, y_sys2 + 4, bar_w, 16, frac)

        y_sys3 = y_sys2 + cfg["sm_sz"] + 10
        if disk is not None:
            used_g, total_g = disk
            frac = used_g / total_g if total_g > 0 else 0
            d.text((pad, y_sys3), f"Disk: {used_g:.1f}/{total_g:.1f}GB", font=cfg["FONT_SM"], fill="white")
            draw_bar(d, bar_x, y_sys3 + 4, bar_w, 16, frac)

        # write to framebuffer (no external processes)
        write_to_fb(img, W, H, BPP)

        # sleep remainder
        dt = time.time() - t0
        time.sleep(max(0.1, REFRESH_EVERY_SEC - dt))


def main():
    while True:
        try:
            render_loop()
        except Exception:
            # if fb rotated / mode changed, re-detect
            time.sleep(2)


if __name__ == "__main__":
    main()