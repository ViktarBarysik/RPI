#!/usr/bin/env python3
import os, time, json, socket, shutil, subprocess
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont

FB_DEV = "/dev/fb0"
IMG_PATH = "/tmp/lcd_dashboard.png"
CACHE_PATH = "/tmp/batumi_weather_cache.json"

BATUMI_LAT = 41.6168
BATUMI_LON = 41.6367

REFRESH_EVERY_SEC = 10
WEATHER_REFRESH_SEC = 10 * 60

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
    61: ("Rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    71: ("Snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    80: ("Showers", "rain"),
    81: ("Showers", "rain"),
    82: ("Showers", "rain"),
    95: ("Thunderstorm", "thunder"),
    96: ("Thunderstorm", "thunder"),
    99: ("Thunderstorm", "thunder"),
}

def fb_geometry():
    """
    Return (W,H) detected from fbset. Fallback (480,320).
    """
    try:
        out = subprocess.check_output(["fbset", "-fb", FB_DEV, "-s"], text=True, timeout=2)
        # line like: geometry 320 480 320 480 16
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("geometry"):
                parts = line.split()
                w = int(parts[1]); h = int(parts[2])
                return w, h
    except Exception:
        pass
    return 480, 320

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "N/A"

def get_public_ip():
    try:
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
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
    r = requests.get(url, params=params, timeout=6)
    r.raise_for_status()
    data = r.json()
    cw = data.get("current_weather", {})
    temp = cw.get("temperature")
    wind = cw.get("windspeed")
    code = cw.get("weathercode")
    desc, icon = WEATHER_CODE.get(code, (f"Code {code}", "cloud"))
    return {"ts": int(time.time()), "temp": temp, "wind": wind, "code": code, "desc": desc, "icon": icon}

def get_batumi_weather_cached():
    try:
        if os.path.exists(CACHE_PATH):
            cache = json.load(open(CACHE_PATH, "r"))
            if int(time.time()) - int(cache.get("ts", 0)) < WEATHER_REFRESH_SEC:
                return cache
    except Exception:
        pass
    try:
        w = fetch_batumi_weather()
        json.dump(w, open(CACHE_PATH, "w"))
        return w
    except Exception:
        try:
            if os.path.exists(CACHE_PATH):
                return json.load(open(CACHE_PATH, "r"))
        except Exception:
            pass
        return {"temp": None, "wind": None, "code": None, "desc": "N/A", "icon": "cloud"}

def clamp(s: str, maxlen: int):
    s = s if s is not None else ""
    return (s[:maxlen-1] + "…") if len(s) > maxlen else s

def draw_bar(draw, x, y, w, h, frac):
    draw.rectangle([x, y, x+w, y+h], outline="gray", width=2)
    frac = max(0.0, min(1.0, frac))
    draw.rectangle([x+2, y+2, x+2+int((w-4)*frac), y+h-2], fill="white")

# --- Weather icons (simple vector-ish) ---
def icon_sun(d, x, y, s):
    r = s // 3
    d.ellipse([x+r, y+r, x+s-r, y+s-r], outline="yellow", width=4)
    # rays
    cx, cy = x + s//2, y + s//2
    for dx, dy in [(0,-1),(1,0),(0,1),(-1,0),(1,-1),(1,1),(-1,1),(-1,-1)]:
        d.line([cx, cy, cx + dx*(s//2), cy + dy*(s//2)], fill="yellow", width=3)

def icon_cloud(d, x, y, s):
    # simple cloud from circles + base
    d.ellipse([x+s*0.05, y+s*0.40, x+s*0.45, y+s*0.85], outline="white", width=3)
    d.ellipse([x+s*0.25, y+s*0.20, x+s*0.70, y+s*0.80], outline="white", width=3)
    d.ellipse([x+s*0.55, y+s*0.40, x+s*0.95, y+s*0.85], outline="white", width=3)
    d.rectangle([x+s*0.12, y+s*0.60, x+s*0.88, y+s*0.88], outline="white", width=3)

def icon_rain(d, x, y, s):
    icon_cloud(d, x, y, s)
    for i in range(4):
        xx = x + int(s*(0.22 + i*0.18))
        d.line([xx, y+int(s*0.88), xx-8, y+s+10], fill="cyan", width=3)

def icon_thunder(d, x, y, s):
    icon_cloud(d, x, y, s)
    # lightning bolt
    bolt = [
        (x+int(s*0.55), y+int(s*0.85)),
        (x+int(s*0.45), y+int(s*1.05)),
        (x+int(s*0.58), y+int(s*1.05)),
        (x+int(s*0.42), y+int(s*1.28)),
    ]
    d.line(bolt, fill="yellow", width=4)

def icon_snow(d, x, y, s):
    icon_cloud(d, x, y, s)
    # simple snowflakes
    for i in range(3):
        cx = x + int(s*(0.30 + i*0.22))
        cy = y + int(s*1.05)
        d.line([cx-10, cy, cx+10, cy], fill="white", width=2)
        d.line([cx, cy-10, cx, cy+10], fill="white", width=2)
        d.line([cx-7, cy-7, cx+7, cy+7], fill="white", width=2)
        d.line([cx-7, cy+7, cx+7, cy-7], fill="white", width=2)

def draw_weather_icon(d, kind, x, y, s):
    if kind == "sun":
        icon_sun(d, x, y, s)
    elif kind == "cloud_sun":
        icon_sun(d, x, y, s)
        icon_cloud(d, x+int(s*0.20), y+int(s*0.25), s)
    elif kind == "rain":
        icon_rain(d, x, y, s)
    elif kind == "thunder":
        icon_thunder(d, x, y, s)
    elif kind == "snow":
        icon_snow(d, x, y, s)
    else:
        icon_cloud(d, x, y, s)

def render_once():
    W, H = fb_geometry()

    # Fonts scaled for both 480x320 and 320x480
    if W >= 480 and H <= 320:
        title_sz, med_sz, sm_sz = 36, 22, 18
        icon_size = 80
        pad = 14
    else:
        title_sz, med_sz, sm_sz = 34, 22, 18
        icon_size = 90
        pad = 14

    FONT_TITLE = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", title_sz)
    FONT_MED   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", med_sz)
    FONT_SM    = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", sm_sz)

    now = datetime.now().strftime("%H:%M:%S  %d %b %Y")
    local_ip = get_local_ip()
    public_ip = get_public_ip()
    iface = get_default_iface()
    link = "UP" if iface != "N/A" and iface_is_up(iface) else "DOWN"

    cpu_t = read_cpu_temp_c()
    load = read_load()
    mem = read_mem()
    disk = read_disk_root()
    weather = get_batumi_weather_cached()

    img = Image.new("RGB", (W, H), "black")
    d = ImageDraw.Draw(img)

    # Header
    d.text((pad, pad), "Batumi", font=FONT_TITLE, fill="white")
    d.text((pad, pad + title_sz + 2), now, font=FONT_SM, fill="gray")

    # Weather block (icon + compact text)
    y_weather = pad + title_sz + 26
    draw_weather_icon(d, weather.get("icon", "cloud"), W - icon_size - pad, y_weather, icon_size)

    desc = clamp(weather.get("desc", "N/A"), 22 if W < 480 else 26)
    temp = weather.get("temp")
    wind = weather.get("wind")

    temp_str = f"{temp:.1f}°C" if isinstance(temp, (int, float)) else "N/A"
    wind_str = f"{wind:.0f} km/h" if isinstance(wind, (int, float)) else "N/A"

    d.text((pad, y_weather), f"Weather: {desc}", font=FONT_MED, fill="cyan")
    d.text((pad, y_weather + med_sz + 6), f"Temp: {temp_str}", font=FONT_MED, fill="cyan")
    d.text((pad, y_weather + (med_sz + 6)*2), f"Wind: {wind_str}", font=FONT_MED, fill="cyan")

    # Network block
    y_net = y_weather + (med_sz + 6)*3 + 10
    d.text((pad, y_net), f"IF: {iface} ({link})", font=FONT_SM, fill="white")
    d.text((pad, y_net + sm_sz + 6), f"LAN : {clamp(local_ip, 32)}", font=FONT_SM, fill="white")
    d.text((pad, y_net + (sm_sz + 6)*2), f"WAN : {clamp(public_ip, 32)}", font=FONT_SM, fill="white")

    # System block (bars)
    y_sys = y_net + (sm_sz + 6)*3 + 8
    if cpu_t is not None:
        d.text((pad, y_sys), f"CPU: {cpu_t:.1f}°C", font=FONT_SM, fill="white")
    else:
        d.text((pad, y_sys), "CPU: N/A", font=FONT_SM, fill="white")

    if load is not None:
        d.text((pad + 160, y_sys), f"Load: {load[0]:.2f}", font=FONT_SM, fill="white")

    y_sys2 = y_sys + sm_sz + 8
    if mem is not None:
        used, total = mem
        frac = used / total if total > 0 else 0
        d.text((pad, y_sys2), f"RAM: {used:.0f}/{total:.0f}MB", font=FONT_SM, fill="white")
        draw_bar(d, pad + 180, y_sys2 + 4, W - (pad + 180) - pad, 16, frac)

    y_sys3 = y_sys2 + sm_sz + 10
    if disk is not None:
        used_g, total_g = disk
        frac = used_g / total_g if total_g > 0 else 0
        d.text((pad, y_sys3), f"Disk: {used_g:.1f}/{total_g:.1f}GB", font=FONT_SM, fill="white")
        draw_bar(d, pad + 180, y_sys3 + 4, W - (pad + 180) - pad, 16, frac)

    img.save(IMG_PATH)

    # IMPORTANT: no "-a" to avoid scaling/cropping
    subprocess.run(
        ["fbi", "-T", "1", "-d", FB_DEV, "-noverbose", IMG_PATH],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def main():
    while True:
        try:
            render_once()
        except Exception:
            pass
        time.sleep(REFRESH_EVERY_SEC)

if __name__ == "__main__":
    main()