import base64
import io
import json
import os
import socket
import sys
import threading
import time
import wave
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template_string, request
from pydexcom import Dexcom
import pychromecast
from pychromecast.controllers.dashcast import DashCastController, APP_DASHCAST

# Ställ in UTF-8 i Windows-konsolen för trendpilar
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# --- Sökvägar ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DATA_FILE = os.path.join(BASE_DIR, "data.json")

# --- Mappning av trendpilar ---
TREND_ARROWS = {
    "DoubleUp": "⇈",
    "SingleUp": "↑",
    "FortyFiveUp": "↗",
    "Flat": "→",
    "FortyFiveDown": "↘",
    "SingleDown": "↓",
    "DoubleDown": "⇊",
    "None": "",
    "NotComputable": "?",
    "RateOutOfRange": "-",
    "rising quickly": "⇈",
    "rising": "↑",
    "rising slightly": "↗",
    "steady": "→",
    "falling slightly": "↘",
    "falling": "↓",
    "falling quickly": "⇊",
    "unable to determine trend": "?",
    "trend unavailable": "-",
    "DOUBLE_UP": "⇈",
    "SINGLE_UP": "↑",
    "FORTY_FIVE_UP": "↗",
    "FLAT": "→",
    "FORTY_FIVE_DOWN": "↘",
    "SINGLE_DOWN": "↓",
    "DOUBLE_DOWN": "⇊",
}

def get_trend_arrow(reading):
    """Extraherar korrekt trendpil från pydexcom GlucoseReading."""
    if not reading:
        return "→"
    direction = getattr(reading, "trend_direction", None)
    if direction and direction in TREND_ARROWS:
        return TREND_ARROWS[direction]
    description = getattr(reading, "trend_description", None)
    if description and description in TREND_ARROWS:
        return TREND_ARROWS[description]
    return getattr(reading, "trend_arrow", "") or "→"

def get_local_ip():
    """Detekterar serverns lokala IP-adress på nätverket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def generate_silent_wav():
    """Genererar 0.5 sekunder ren digital tystnad i minnet för keep-alive."""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b'\x00\x00' * 4000)
    return buf.getvalue()

SILENT_WAV_BYTES = generate_silent_wav()

# --- Konfigurationshantering ---
DEFAULT_CONFIG = {
    "dexcom_username": "",
    "dexcom_password": "",
    "dexcom_region": "ous",
    "hub_name": "",
    "hub_ip": "",
    "server_port": 8080
}

config_lock = threading.RLock()

def load_config():
    with config_lock:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    for k, v in DEFAULT_CONFIG.items():
                        if k not in cfg:
                            cfg[k] = v
                    return cfg
            except Exception as e:
                print(f"Kunde inte läsa config.json: {e}", file=sys.stderr)
        # Skapa default om den saknas
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

def save_config(new_config):
    with config_lock:
        temp_file = CONFIG_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=2, ensure_ascii=False)
        os.replace(temp_file, CONFIG_FILE)

def calculate_delta(current_val, prev_val):
    diff = round(current_val - prev_val, 1)
    if diff > 0:
        return f"+{diff}"
    elif diff < 0:
        return f"{diff}"
    return "±0.0"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_data(data):
    temp_file = DATA_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(temp_file, DATA_FILE)


# ==============================================================================
# BAKGRUNDSTRÅD 1: DEXCOM FETCHER (Hybrid 12h historik + 1 min kontroll)
# ==============================================================================
def dexcom_worker():
    print("[Dexcom] Startar hämtningstråd...", flush=True)
    dexcom_client = None
    last_user = None
    last_pass = None
    last_region = None

    while True:
        try:
            cfg = load_config()
            username = cfg.get("dexcom_username", "").strip()
            password = cfg.get("dexcom_password", "").strip()
            region = cfg.get("dexcom_region", "ous").strip()

            if not username or not password:
                print("[Dexcom] Inloggningsuppgifter saknas i config. Gå till /config för att fylla i.", flush=True)
                time.sleep(15)
                continue

            # Logga in på nytt om uppgifterna ändrats eller saknas
            if dexcom_client is None or username != last_user or password != last_pass or region != last_region:
                print(f"[Dexcom] Loggar in som '{username}' (Region: {region})...", flush=True)
                dexcom_client = Dexcom(username=username, password=password, region=region)
                last_user, last_pass, last_region = username, password, region
                print("[Dexcom] Ansluten mot Dexcom Share!", flush=True)

            # Kör hybridhämtning
            prev_data = load_data()
            history = prev_data.get("history", []) if prev_data else []

            # 1. Bygg 12h historik om den saknas
            if not history or len(history) < 10:
                print("[Dexcom] Hämtar 12h historik...", flush=True)
                readings = dexcom_client.get_glucose_readings(max_count=150, minutes=720)
                if not readings:
                    time.sleep(60)
                    continue

                current = readings[0]
                prev = readings[1] if len(readings) > 1 else None

                current_val = round(current.mmol_l, 1) if hasattr(current, "mmol_l") else round(current.value / 18.0182, 1)
                trend_arrow = get_trend_arrow(current)
                trend_desc = current.trend_description
                current_epoch = int(current.datetime.timestamp())
                reading_time_str = current.datetime.strftime("%H:%M")

                delta_str = "±0.0"
                if prev:
                    prev_val = round(prev.mmol_l, 1) if hasattr(prev, "mmol_l") else round(prev.value / 18.0182, 1)
                    delta_str = calculate_delta(current_val, prev_val)

                history = [
                    {
                        "value": round(r.mmol_l, 1) if hasattr(r, "mmol_l") else round(r.value / 18.0182, 1),
                        "time": r.datetime.strftime("%H:%M"),
                        "epoch": int(r.datetime.timestamp())
                    }
                    for r in reversed(readings)
                ]
                print(f"[Dexcom] 12h historik inläst ({len(history)} punkter).", flush=True)

            # 2. Snabb 1-punkts kontroll (var minut)
            else:
                current = dexcom_client.get_current_glucose_reading()
                if not current:
                    time.sleep(60)
                    continue

                current_epoch = int(current.datetime.timestamp())
                prev_epoch = prev_data.get("reading_epoch", 0)
                current_val = round(current.mmol_l, 1) if hasattr(current, "mmol_l") else round(current.value / 18.0182, 1)
                trend_arrow = get_trend_arrow(current)
                trend_desc = current.trend_description
                reading_time_str = current.datetime.strftime("%H:%M")

                if current_epoch > prev_epoch:
                    prev_val = float(prev_data.get("value", current_val))
                    delta_str = calculate_delta(current_val, prev_val)

                    if (current_epoch - prev_epoch) > 1800:
                        fresh_readings = dexcom_client.get_glucose_readings(max_count=150, minutes=720)
                        if fresh_readings:
                            history = [
                                {
                                    "value": round(r.mmol_l, 1) if hasattr(r, "mmol_l") else round(r.value / 18.0182, 1),
                                    "time": r.datetime.strftime("%H:%M"),
                                    "epoch": int(r.datetime.timestamp())
                                }
                                for r in reversed(fresh_readings)
                            ]
                    else:
                        history.append({
                            "value": current_val,
                            "time": reading_time_str,
                            "epoch": current_epoch
                        })
                        cutoff = current_epoch - 12 * 3600
                        history = [p for p in history if p.get("epoch", 0) >= cutoff]

                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Ny avläsning ({reading_time_str}): {current_val} mmol/L ({trend_arrow}) | Delta: {delta_str}", flush=True)
                else:
                    delta_str = prev_data.get("delta", "±0.0")

            reading_dt = current.datetime
            now = datetime.now(timezone.utc) if reading_dt.tzinfo else datetime.now()
            age_seconds = int((now - reading_dt).total_seconds())
            age_minutes = max(0, age_seconds // 60)

            data_obj = {
                "value": current_val,
                "trend": trend_desc,
                "trend_symbol": trend_arrow,
                "delta": delta_str,
                "timestamp": reading_time_str,
                "reading_epoch": current_epoch,
                "minutes_ago": age_minutes,
                "minutes_ago_text": f"{age_minutes} min sedan",
                "system_updated": datetime.now().strftime("%H:%M:%S"),
                "history": history
            }
            save_data(data_obj)

        except Exception as e:
            print(f"[Dexcom] Fel vid hämtning: {e}", file=sys.stderr, flush=True)
            dexcom_client = None

        time.sleep(60)


# ==============================================================================
# BAKGRUNDSTRÅD 2: CAST KEEP-ALIVE (Nest Hub övervakare)
# ==============================================================================
def is_media_active(cast):
    try:
        mc = cast.media_controller
        state = mc.status.player_state
        if state in ("PLAYING", "BUFFERING", "PAUSED"):
            return True
        if mc.status.player_is_playing or mc.status.player_is_paused:
            return True
    except Exception:
        pass
    return False

def is_hub_idle(cast):
    if is_media_active(cast):
        return False
    app_id = (cast.app_id or "").lower()
    display_name = (cast.status.display_name or "").lower()
    IDLE_NAMES = {"backdrop", "ambient", "", "none"}
    IDLE_IDS = {"e8c28d3c", None, ""}
    return display_name in IDLE_NAMES or app_id in IDLE_IDS

def cast_worker():
    print("[Cast] Startar Keep-Alive övervakare...", flush=True)
    cast = None
    dashcast = None
    browser = None

    while True:
        try:
            cfg = load_config()
            hub_name = cfg.get("hub_name", "").strip()
            hub_ip = cfg.get("hub_ip", "").strip()
            port = cfg.get("server_port", 8080)
            local_ip = get_local_ip()
            target_url = f"http://{local_ip}:{port}/"

            if not hub_name and not hub_ip:
                time.sleep(10)
                continue

            # Säkerställ anslutning
            if cast is None or not cast.socket_client.is_connected:
                if cast:
                    try:
                        cast.disconnect()
                    except Exception:
                        pass
                if browser:
                    try:
                        pychromecast.discovery.stop_discovery(browser)
                    except Exception:
                        pass

                hosts = [hub_ip] if hub_ip else None
                names = [hub_name] if hub_name else None

                chromecasts, browser = pychromecast.get_listed_chromecasts(
                    friendly_names=names,
                    known_hosts=hosts,
                    discovery_timeout=4
                )

                if not chromecasts:
                    cast = None
                    browser = None
                    time.sleep(10)
                    continue

                cast = chromecasts[0]
                cast.wait(timeout=10)
                dashcast = DashCastController()
                cast.register_handler(dashcast)
                target_name = cast.name or hub_name or hub_ip or "Cast-enhet"
                print(f"[Cast] Ansluten till enhet: '{target_name}' ({hub_ip or cast.cast_info.host})!", flush=True)

            # Kontrollera status på enheten
            app_id = cast.app_id
            display_name = cast.status.display_name or "Okänd"
            target_name = cast.name or hub_name or hub_ip or "Cast-enhet"

            if is_media_active(cast):
                pass  # Musik/film spelas, avbryt inte
            elif app_id == APP_DASHCAST:
                pass  # Visar redan vår sida
            elif is_hub_idle(cast):
                print(f"[{time.strftime('%H:%M:%S')}][Cast] Enheten '{target_name}' är redo ({display_name}). Castar display till enhet: {target_url}", flush=True)
                dashcast.load_url(target_url, force=True, reload_seconds=0)
                time.sleep(4)
            else:
                pass

        except Exception as e:
            cast = None
            time.sleep(10)

        time.sleep(10)


# ==============================================================================
# BAKGRUNDSTRÅD 3: CAST DISCOVERY CACHE (Söker enheter vid start)
# ==============================================================================
discovered_cast_devices = []
cast_discovery_lock = threading.Lock()
is_discovering = False

def run_cast_discovery():
    global discovered_cast_devices, is_discovering
    with cast_discovery_lock:
        if is_discovering:
            return
        is_discovering = True

    try:
        print("[Cast Discovery] Söker efter Cast-enheter på lokala nätverket...", flush=True)
        chromecasts, browser = pychromecast.get_chromecasts(timeout=4)
        devs = []
        for c in chromecasts:
            devs.append({
                "name": c.name,
                "host": c.cast_info.host,
                "model": c.model_name or "Cast Device"
            })
        pychromecast.discovery.stop_discovery(browser)
        with cast_discovery_lock:
            discovered_cast_devices = devs
            is_discovering = False
        print(f"[Cast Discovery] Sökning klar! Hittade {len(devs)} enhet(er).", flush=True)
    except Exception as e:
        with cast_discovery_lock:
            is_discovering = False
        print(f"[Cast Discovery] Fel vid sökning: {e}", file=sys.stderr, flush=True)


# ==============================================================================
# FLASK WEBSERVER & MALLAR (Inbäddade direkt i filen)
# ==============================================================================
app = Flask(__name__)

INDEX_HTML = """<!DOCTYPE html>
<html lang="sv">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Blodsocker</title>
  <style>
    :root {
      --color-normal: #ffffff;
      --color-low: #ff453a;
      --color-high: #ff9f0a;
      --color-subtle: #8e8e93;
      --color-green: #30d158;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: #000000;
      color: var(--color-normal);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Rounded", "SF Pro Display", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      margin: 0; padding: 0;
      width: 100vw; height: 100vh;
      overflow: hidden; user-select: none;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      touch-action: pan-y pinch-zoom;
    }
    .slider-wrapper {
      display: flex;
      width: 200vw; height: 100vh;
      transition: transform 0.45s cubic-bezier(0.25, 1, 0.5, 1);
    }
    .slide {
      width: 100vw; height: 100vh;
      display: flex; flex-direction: column;
      justify-content: center; align-items: center;
      position: relative;
      padding: 2vh 3vw 4.5vh 3vw;
    }
    .config-btn {
      position: fixed; top: 1.5vh; right: 2vw;
      width: 38px; height: 38px; border-radius: 50%;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.15);
      display: flex; align-items: center; justify-content: center;
      color: #8e8e93; text-decoration: none; font-size: 20px;
      z-index: 200; backdrop-filter: blur(8px);
      transition: background 0.2s;
    }
    .config-btn:hover { background: rgba(255, 255, 255, 0.2); color: #fff; }

    /* SIDA 1 */
    .main-display {
      display: flex; align-items: center; justify-content: center;
      gap: 2.5vw; transition: transform 0.3s ease;
    }
    .glucose-value {
      font-size: min(42vw, 54vh); font-weight: 750;
      line-height: 0.95; letter-spacing: -0.04em;
      font-variant-numeric: tabular-nums lining-nums;
      transition: color 0.4s ease, text-shadow 0.4s ease;
    }
    .trend-arrow {
      font-size: min(26vw, 33vh); line-height: 1;
      display: inline-flex; align-items: center; justify-content: center;
      transition: color 0.4s ease, text-shadow 0.4s ease, opacity 0.4s ease;
    }
    .status-normal .glucose-value, .status-normal .trend-arrow {
      color: var(--color-normal); text-shadow: 0 0 35px rgba(255, 255, 255, 0.12);
    }
    .status-low .glucose-value, .status-low .trend-arrow {
      color: var(--color-low); text-shadow: 0 0 50px rgba(255, 69, 58, 0.35);
    }
    .status-high .glucose-value, .status-high .trend-arrow {
      color: var(--color-high); text-shadow: 0 0 50px rgba(255, 159, 10, 0.35);
    }
    .status-no-data .glucose-value {
      font-size: min(12vw, 15vh); font-weight: 750;
      letter-spacing: 0.04em; color: var(--color-normal);
      text-shadow: 0 0 35px rgba(255, 255, 255, 0.12);
    }
    .status-no-data .trend-arrow { display: none; }
    .details {
      display: flex; align-items: center; gap: 2.5vw;
      margin-top: 2.5vh; padding: 1.2vh 3.5vw;
      border-radius: 9999px; background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.1);
      font-size: min(10vw, 9.5vh); font-weight: 500;
      color: var(--color-subtle); box-shadow: 0 4px 24px rgba(0, 0, 0, 0.5);
      font-variant-numeric: tabular-nums;
    }
    .delta { color: #f2f2f7; font-weight: 600; }
    .separator { color: #3a3a3c; font-size: 0.8em; }
    .time-ago { color: #aeaeb2; }

    /* SIDA 2 */
    .slide-2-content {
      width: 100%; height: 100%;
      display: flex; flex-direction: column;
      justify-content: space-between; align-items: center;
      max-width: 980px;
    }
    .stats-header {
      width: 100%; display: grid; grid-template-columns: repeat(4, 1fr);
      gap: 1.5vw; margin-bottom: 1.5vh;
    }
    .stat-card {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.09);
      border-radius: 16px; padding: 1.2vh 1.5vw;
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      backdrop-filter: blur(10px);
    }
    .stat-label {
      font-size: min(2.5vw, 2.2vh); color: var(--color-subtle);
      font-weight: 500; margin-bottom: 0.4vh; text-transform: uppercase; letter-spacing: 0.03em;
    }
    .stat-value {
      font-size: min(5.5vw, 4.8vh); font-weight: 750;
      font-variant-numeric: tabular-nums; line-height: 1;
    }
    .text-green { color: var(--color-green); }
    .text-red { color: var(--color-low); }
    .text-orange { color: var(--color-high); }
    .chart-container { width: 100%; flex: 1; min-height: 0; position: relative; }
    #glucose-chart { width: 100%; height: 100%; display: block; }

    /* Dots */
    .page-dots {
      position: fixed; bottom: 1.5vh; left: 50%;
      transform: translateX(-50%); display: flex; align-items: center; gap: 8px; z-index: 100;
    }
    .dot {
      width: 8px; height: 8px; border-radius: 9999px;
      background: rgba(255, 255, 255, 0.22); transition: all 0.3s ease; cursor: pointer;
    }
    .dot.active { width: 24px; background: rgba(255, 255, 255, 0.85); }
    .stale { opacity: 0.4; filter: grayscale(1); }
  </style>
</head>
<body>
  <a href="/config" class="config-btn" title="Inställningar">⚙</a>
  <div class="slider-wrapper" id="slider">
    <!-- SIDA 1 -->
    <section class="slide" id="slide-1">
      <div id="container" class="main-display status-normal">
        <div id="value" class="glucose-value">--</div>
        <div id="arrow" class="trend-arrow"></div>
      </div>
      <div class="details">
        <div id="delta" class="delta">±0.0</div>
        <div class="separator">•</div>
        <div id="time-ago" class="time-ago">-- min sedan</div>
      </div>
    </section>

    <!-- SIDA 2 -->
    <section class="slide" id="slide-2">
      <div class="slide-2-content">
        <div class="stats-header">
          <div class="stat-card">
            <span class="stat-label">Inom mål (4–10)</span>
            <span class="stat-value text-green" id="tir-percent">--%</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">Lågt (&lt;4.0)</span>
            <span class="stat-value text-red" id="low-percent">--%</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">Högt (&gt;10.0)</span>
            <span class="stat-value text-orange" id="high-percent">--%</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">12h Snitt</span>
            <span class="stat-value" id="avg-value">--</span>
          </div>
        </div>
        <div class="chart-container">
          <canvas id="glucose-chart"></canvas>
        </div>
      </div>
    </section>
  </div>

  <div class="page-dots">
    <div class="dot active" id="dot-1" onclick="goToSlide(0)"></div>
    <div class="dot" id="dot-2" onclick="goToSlide(1)"></div>
  </div>

  <audio id="silence-audio" loop playsinline autoplay src="/silence.wav" preload="auto" style="display:none;"></audio>

  <script>
    let latestData = null;
    let currentSlide = 0;
    let autoReturnTimer = null;
    const AUTO_RETURN_DELAY = 60000;

    function goToSlide(index) {
      currentSlide = index;
      const slider = document.getElementById('slider');
      slider.style.transform = `translateX(-${currentSlide * 100}vw)`;
      document.getElementById('dot-1').classList.toggle('active', currentSlide === 0);
      document.getElementById('dot-2').classList.toggle('active', currentSlide === 1);

      if (currentSlide === 1) {
        if (latestData && latestData.history) {
          drawChart(latestData.history);
          updateTirStats(latestData.history);
        }
        resetAutoReturnTimer();
      } else {
        clearTimeout(autoReturnTimer);
        autoReturnTimer = null;
      }
    }

    function resetAutoReturnTimer() {
      clearTimeout(autoReturnTimer);
      if (currentSlide !== 0) {
        autoReturnTimer = setTimeout(() => { goToSlide(0); }, AUTO_RETURN_DELAY);
      }
    }

    let touchStartX = 0;
    let touchStartY = 0;

    window.addEventListener('touchstart', (e) => {
      touchStartX = e.changedTouches[0].clientX;
      touchStartY = e.changedTouches[0].clientY;
      if (currentSlide === 1) resetAutoReturnTimer();
    }, { passive: true });

    window.addEventListener('touchend', (e) => {
      const diffX = e.changedTouches[0].clientX - touchStartX;
      const diffY = e.changedTouches[0].clientY - touchStartY;
      if (Math.abs(diffX) > Math.abs(diffY) && Math.abs(diffX) > 40) {
        if (diffX < 0 && currentSlide === 0) goToSlide(1);
        else if (diffX > 0 && currentSlide === 1) goToSlide(0);
      }
    }, { passive: true });

    window.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight') goToSlide(1);
      if (e.key === 'ArrowLeft') goToSlide(0);
    });

    function initSilentAudio() {
      try {
        const audio = document.getElementById('silence-audio');
        if (audio) { audio.volume = 0.05; audio.play().catch(() => {}); }
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) {
          const ctx = new AudioCtx();
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          gain.gain.value = 0;
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start();
        }
      } catch (e) {}
    }
    initSilentAudio();
    window.addEventListener('click', initSilentAudio, { once: true });
    window.addEventListener('touchstart', initSilentAudio, { once: true });

    function updateTirStats(history) {
      if (!history || history.length === 0) return;
      const total = history.length;
      const inRange = history.filter(h => h.value >= 4.0 && h.value <= 10.0).length;
      const low = history.filter(h => h.value < 4.0).length;
      const high = history.filter(h => h.value > 10.0).length;
      document.getElementById('tir-percent').textContent = `${Math.round((inRange / total) * 100)}%`;
      document.getElementById('low-percent').textContent = `${Math.round((low / total) * 100)}%`;
      document.getElementById('high-percent').textContent = `${Math.round((high / total) * 100)}%`;
      document.getElementById('avg-value').textContent = (history.reduce((a, b) => a + b.value, 0) / total).toFixed(1);
    }

    function drawChart(history) {
      const canvas = document.getElementById('glucose-chart');
      if (!canvas || !history || history.length === 0) return;
      const ctx = canvas.getContext('2d');
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);

      const width = rect.width;
      const height = rect.height;
      ctx.clearRect(0, 0, width, height);

      const padLeft = 44, padRight = 18, padTop = 15, padBottom = 26;
      const plotWidth = width - padLeft - padRight;
      const plotHeight = height - padTop - padBottom;
      if (plotWidth <= 0 || plotHeight <= 0) return;

      const values = history.map(h => h.value);
      const minVal = Math.min(...values, 3.5);
      const maxVal = Math.max(...values, 11.0);
      const yMin = Math.max(2.0, Math.floor(minVal - 0.5));
      const yMax = Math.min(24.0, Math.ceil(maxVal + 0.8));
      const getY = (v) => padTop + plotHeight - ((v - yMin) / (yMax - yMin)) * plotHeight;

      const y10 = getY(10.0);
      const y4 = getY(4.0);

      ctx.fillStyle = 'rgba(255, 255, 255, 0.03)';
      ctx.fillRect(padLeft, y10, plotWidth, y4 - y10);

      // 10.0 linje
      ctx.beginPath();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = 'rgba(255, 159, 10, 0.55)';
      ctx.lineWidth = 1.5;
      ctx.moveTo(padLeft, y10); ctx.lineTo(padLeft + plotWidth, y10);
      ctx.stroke();
      ctx.fillStyle = '#ff9f0a';
      ctx.font = '600 12px -apple-system, sans-serif';
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.fillText('10.0', padLeft - 6, y10);

      // 4.0 linje
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(255, 69, 58, 0.55)';
      ctx.moveTo(padLeft, y4); ctx.lineTo(padLeft + plotWidth, y4);
      ctx.stroke();
      ctx.fillStyle = '#ff453a';
      ctx.fillText('4.0', padLeft - 6, y4);

      // X-axel
      const nowEpoch = Math.floor(Date.now() / 1000);
      const span = 12 * 3600;
      const startEpoch = nowEpoch - span;
      const getX = (e) => padLeft + Math.max(0, Math.min(1, (e - startEpoch) / span)) * plotWidth;

      ctx.setLineDash([]);
      ctx.fillStyle = '#636366';
      ctx.font = '500 11px -apple-system, sans-serif';
      ctx.textBaseline = 'top';

      const fmtTime = (e) => {
        const d = new Date(e * 1000);
        return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
      };

      const ticks = [
        { epoch: startEpoch, label: fmtTime(startEpoch), align: 'left' },
        { epoch: startEpoch + 4 * 3600, label: fmtTime(startEpoch + 4 * 3600), align: 'center' },
        { epoch: startEpoch + 8 * 3600, label: fmtTime(startEpoch + 8 * 3600), align: 'center' },
        { epoch: nowEpoch, label: 'Nu', align: 'right' }
      ];
      ticks.forEach(t => {
        ctx.textAlign = t.align;
        ctx.fillText(t.label, getX(t.epoch), padTop + plotHeight + 8);
      });

      const points = history.map(h => ({ x: getX(h.epoch), y: getY(h.value), val: h.value }));
      if (points.length === 0) return;

      const grad = ctx.createLinearGradient(0, padTop, 0, padTop + plotHeight);
      grad.addColorStop(0, 'rgba(255, 255, 255, 0.12)');
      grad.addColorStop(1, 'rgba(255, 255, 255, 0.0)');

      ctx.beginPath();
      ctx.moveTo(points[0].x, padTop + plotHeight);
      points.forEach(p => ctx.lineTo(p.x, p.y));
      ctx.lineTo(points[points.length - 1].x, padTop + plotHeight);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);
      for (let i = 1; i < points.length; i++) ctx.lineTo(points[i].x, points[i].y);
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.75)';
      ctx.lineWidth = 2.2;
      ctx.stroke();

      points.forEach(p => {
        ctx.beginPath();
        let col = '#f5f5f7';
        if (p.val < 4.0) col = '#ff453a';
        else if (p.val > 10.0) col = '#ff9f0a';
        ctx.fillStyle = col;
        ctx.arc(p.x, p.y, 2.2, 0, Math.PI * 2);
        ctx.fill();
      });

      const latest = points[points.length - 1];
      let halo = 'rgba(255, 255, 255, 0.35)', center = '#ffffff';
      if (latest.val < 4.0) { halo = 'rgba(255, 69, 58, 0.45)'; center = '#ff453a'; }
      else if (latest.val > 10.0) { halo = 'rgba(255, 159, 10, 0.45)'; center = '#ff9f0a'; }

      ctx.beginPath(); ctx.fillStyle = halo; ctx.arc(latest.x, latest.y, 7, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.fillStyle = center; ctx.arc(latest.x, latest.y, 3.5, 0, Math.PI * 2); ctx.fill();
    }

    function updateDisplay() {
      if (!latestData || !latestData.reading_epoch) return;
      const nowEpoch = Math.floor(Date.now() / 1000);
      const diffSeconds = Math.max(0, nowEpoch - latestData.reading_epoch);
      const minutes = Math.floor(diffSeconds / 60);
      const seconds = diffSeconds % 60;

      const valueEl = document.getElementById('value');
      const arrowEl = document.getElementById('arrow');
      const deltaEl = document.getElementById('delta');
      const timeAgoEl = document.getElementById('time-ago');
      const containerEl = document.getElementById('container');

      if (minutes < 1) timeAgoEl.textContent = `${seconds}s sedan`;
      else timeAgoEl.textContent = `${minutes} min sedan`;

      const isNoData = diffSeconds >= 11 * 60;
      if (isNoData) {
        containerEl.className = 'main-display status-no-data';
        containerEl.classList.remove('stale');
        valueEl.textContent = 'INGEN DATA';
        arrowEl.textContent = '';
        arrowEl.style.display = 'none';
        const lastVal = parseFloat(latestData.value);
        deltaEl.textContent = !isNaN(lastVal) ? `Senast: ${lastVal.toFixed(1)}` : 'Senast: --';
      } else {
        containerEl.className = 'main-display';
        arrowEl.style.display = '';
        const val = parseFloat(latestData.value);
        valueEl.textContent = !isNaN(val) ? Number(val).toFixed(1) : '--';
        arrowEl.textContent = latestData.trend_symbol || '';
        deltaEl.textContent = latestData.delta || '±0.0';

        if (!isNaN(val)) {
          if (val < 4.0) containerEl.classList.add('status-low');
          else if (val > 10.0) containerEl.classList.add('status-high');
          else containerEl.classList.add('status-normal');
        }
        if (minutes >= 10) containerEl.classList.add('stale');
        else containerEl.classList.remove('stale');
      }

      if (currentSlide === 1 && latestData.history) {
        drawChart(latestData.history);
        updateTirStats(latestData.history);
      }
    }

    async function fetchData() {
      try {
        const res = await fetch('/api/data?t=' + Date.now());
        if (!res.ok) return;
        latestData = await res.json();
        updateDisplay();
      } catch (e) {}
    }

    fetchData();
    setInterval(updateDisplay, 1000);
    setInterval(fetchData, 15000);

    window.addEventListener('resize', () => {
      if (currentSlide === 1 && latestData && latestData.history) drawChart(latestData.history);
    });
  </script>
</body>
</html>
"""

CONFIG_HTML = """<!DOCTYPE html>
<html lang="sv">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Inställningar – Dexcom Webserver</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #09090b;
      --card-bg: #18181b;
      --border: #27272a;
      --text: #f4f4f5;
      --text-muted: #a1a1aa;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --success: #22c55e;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', sans-serif;
      background-color: var(--bg);
      color: var(--text);
      display: flex;
      justify-content: center;
      padding: 40px 16px;
      min-height: 100vh;
    }
    .container {
      width: 100%;
      max-width: 500px;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
    }
    h1 {
      font-size: 20px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .btn-back {
      font-size: 13px;
      color: var(--text-muted);
      text-decoration: none;
      padding: 6px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      transition: all 0.2s;
    }
    .btn-back:hover { color: #fff; border-color: #3f3f46; }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 22px;
      margin-bottom: 20px;
    }
    h2 {
      font-size: 16px;
      font-weight: 600;
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .form-group {
      margin-bottom: 15px;
    }
    label {
      display: block;
      font-size: 13px;
      font-weight: 500;
      color: var(--text-muted);
      margin-bottom: 6px;
    }
    input, select {
      width: 100%;
      padding: 10px 14px;
      background: #09090b;
      border: 1px solid var(--border);
      border-radius: 8px;
      color: #fff;
      font-size: 14px;
      outline: none;
      transition: border 0.2s;
    }
    input:focus, select:focus {
      border-color: var(--primary);
    }
    .btn {
      width: 100%;
      padding: 12px;
      background: var(--primary);
      color: #fff;
      border: none;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
    }
    .btn:hover { background: var(--primary-hover); }
    .btn-scan {
      background: #27272a;
      color: #fff;
      margin-bottom: 12px;
      font-size: 13px;
      padding: 9px;
    }
    .btn-scan:hover { background: #3f3f46; }
    .btn-secondary {
      background: #27272a;
      color: #e4e4e7;
      margin-top: 8px;
      font-size: 13px;
      padding: 10px;
    }
    .btn-secondary:hover { background: #3f3f46; color: #fff; }
    .toast {
      display: none;
      background: var(--success);
      color: #fff;
      padding: 12px;
      border-radius: 8px;
      margin-bottom: 20px;
      font-size: 14px;
      text-align: center;
    }
    .hint {
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 4px;
    }
    .device-list {
      margin-bottom: 12px;
    }
    .spinner {
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 2px solid rgba(255, 255, 255, 0.3);
      border-radius: 50%;
      border-top-color: #fff;
      animation: spin 0.8s linear infinite;
      margin-right: 6px;
      vertical-align: middle;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>⚙ Inställningar</h1>
      <a href="/" class="btn-back">Tillbaka till displayen →</a>
    </div>

    <div id="toast" class="toast">Inställningarna har sparats och tillämpats!</div>

    <form id="configForm" onsubmit="saveSettings(event)">
      <!-- Dexcom -->
      <div class="card">
        <h2>🩸 Dexcom Share Inloggning</h2>
        <div class="form-group">
          <label for="username">Användarnamn</label>
          <input type="text" id="username" name="dexcom_username" value="{{ config.dexcom_username }}" required>
        </div>
        <div class="form-group">
          <label for="password">Lösenord</label>
          <input type="password" id="password" name="dexcom_password" value="{{ config.dexcom_password }}" required>
        </div>
        <div class="form-group">
          <label for="region">Region</label>
          <select id="region" name="dexcom_region">
            <option value="ous" {% if config.dexcom_region == 'ous' %}selected{% endif %}>Europa / Sverige / Övriga världen (ous)</option>
            <option value="us" {% if config.dexcom_region == 'us' %}selected{% endif %}>USA (us)</option>
          </select>
        </div>

        <button type="button" class="btn btn-secondary" id="testDexcomBtn" onclick="testDexcom()">
          🧪 Testa Dexcom-inloggning
        </button>
        <div id="dexcomTestResult" style="display:none; margin-top:12px; padding:12px; border-radius:8px; font-size:14px; line-height:1.5;"></div>
      </div>

      <!-- Cast-enhet -->
      <div class="card">
        <h2>📺 Cast-enhet (Google Nest Hub / Chromecast)</h2>
        
        <div class="form-group device-list" id="deviceListGroup">
          <label>Tillgängliga enheter på nätverket (välj ur listan)</label>
          <select id="deviceSelect" onchange="selectDevice()">
            <option value="">-- Söker efter enheter på nätverket... --</option>
          </select>
        </div>

        <button type="button" class="btn btn-scan" id="scanBtn" onclick="loadCastDevices(true)">
          🔍 Sök efter Cast-enheter igen
        </button>

        <div class="form-group">
          <label for="hubName">Enhetsnamn</label>
          <input type="text" id="hubName" name="hub_name" value="{{ config.hub_name }}" placeholder="t.ex. Google Nest Hub">
        </div>
        <div class="form-group">
          <label for="hubIp">Fast IP-adress (Valfritt men rekommenderat)</label>
          <input type="text" id="hubIp" name="hub_ip" value="{{ config.hub_ip }}" placeholder="t.ex. 192.168.0.66">
          <div class="hint">Ger snabbare direktanslutning utan att behöva söka på nätverket varje gång.</div>
        </div>
      </div>

      <button type="submit" class="btn" id="saveBtn">Spara och Tillämpa</button>
    </form>
  </div>

  <script>
    // Sökningen körs direkt vid serverstart i bakgrunden, så här hämtar vi resultatet omedelbart!
    window.addEventListener('DOMContentLoaded', () => {
      loadCastDevices(false);
    });

    async function testDexcom() {
      const btn = document.getElementById('testDexcomBtn');
      const resultDiv = document.getElementById('dexcomTestResult');
      const username = document.getElementById('username').value.trim();
      const password = document.getElementById('password').value.trim();
      const region = document.getElementById('region').value;

      if (!username || !password) {
        resultDiv.style.display = 'block';
        resultDiv.style.background = 'rgba(239, 68, 68, 0.15)';
        resultDiv.style.color = '#fca5a5';
        resultDiv.style.border = '1px solid #ef4444';
        resultDiv.innerHTML = '⚠️ Fyll i både användarnamn och lösenord först.';
        return;
      }

      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Testar inloggning mot Dexcom Share...';
      resultDiv.style.display = 'none';

      try {
        const res = await fetch('/api/test-dexcom', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password, region })
        });
        const data = await res.json();
        resultDiv.style.display = 'block';

        if (data.success) {
          resultDiv.style.background = 'rgba(34, 197, 94, 0.15)';
          resultDiv.style.color = '#86efac';
          resultDiv.style.border = '1px solid #22c55e';
          if (data.value !== null && data.value !== undefined) {
            resultDiv.innerHTML = `✓ <b>Anslutning lyckades!</b><br>Senaste avläsning: <b>${data.value} mmol/L</b> ${data.arrow || ''} (${data.trend || ''}) kl ${data.time || ''}`;
          } else {
            resultDiv.innerHTML = `✓ <b>Anslutning lyckades!</b> Inga aktuella mätvärden just nu.`;
          }
        } else {
          resultDiv.style.background = 'rgba(239, 68, 68, 0.15)';
          resultDiv.style.color = '#fca5a5';
          resultDiv.style.border = '1px solid #ef4444';
          resultDiv.innerHTML = `❌ <b>Kunde inte ansluta:</b> ${data.error || 'Kontrollera uppgifterna.'}`;
        }
      } catch (err) {
        resultDiv.style.display = 'block';
        resultDiv.style.background = 'rgba(239, 68, 68, 0.15)';
        resultDiv.style.color = '#fca5a5';
        resultDiv.style.border = '1px solid #ef4444';
        resultDiv.innerHTML = `❌ <b>Nätverksfel:</b> ${err.message}`;
      } finally {
        btn.disabled = false;
        btn.innerHTML = '🧪 Testa Dexcom-inloggning';
      }
    }

    async function loadCastDevices(refresh = false) {
      const btn = document.getElementById('scanBtn');
      const select = document.getElementById('deviceSelect');

      if (refresh) {
        btn.innerHTML = '<span class="spinner"></span> Söker på nätverket...';
        btn.disabled = true;
      }

      try {
        const url = refresh ? '/api/discover-cast?refresh=1' : '/api/discover-cast';
        const res = await fetch(url);
        const result = await res.json();
        const devices = result.devices || [];

        select.innerHTML = '<option value="">-- Välj enhet ur listan --</option>';

        if (devices.length > 0) {
          const currentHost = document.getElementById('hubIp').value.trim();
          const currentName = document.getElementById('hubName').value.trim();

          devices.forEach(d => {
            const opt = document.createElement('option');
            opt.value = JSON.stringify(d);
            opt.textContent = `${d.name} (${d.host}) - ${d.model}`;
            if ((currentHost && d.host === currentHost) || (currentName && d.name === currentName)) {
              opt.selected = true;
            }
            select.appendChild(opt);
          });
          if (refresh) btn.innerHTML = `✓ Hittade ${devices.length} enhet(er).`;
        } else if (result.scanning) {
          setTimeout(() => loadCastDevices(false), 1500);
        } else {
          select.innerHTML = '<option value="">-- Inga enheter hittades --</option>';
          if (refresh) btn.innerHTML = 'Inga enheter hittades. Ange manuellt.';
        }
      } catch (e) {
        console.error(e);
        select.innerHTML = '<option value="">-- Fel vid hämtning av enheter --</option>';
      } finally {
        if (refresh) {
          setTimeout(() => {
            btn.disabled = false;
            btn.innerHTML = '🔍 Sök efter Cast-enheter igen';
          }, 3000);
        }
      }
    }

    function selectDevice() {
      const select = document.getElementById('deviceSelect');
      if (!select.value) return;
      const device = JSON.parse(select.value);
      document.getElementById('hubName').value = device.name;
      document.getElementById('hubIp').value = device.host;
    }

    async function saveSettings(e) {
      e.preventDefault();
      const form = document.getElementById('configForm');
      const formData = new FormData(form);
      const data = Object.fromEntries(formData.entries());

      const btn = document.getElementById('saveBtn');
      btn.disabled = true;
      btn.textContent = 'Sparar...';

      try {
        const res = await fetch('/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data)
        });
        if (res.ok) {
          const toast = document.getElementById('toast');
          toast.style.display = 'block';
          setTimeout(() => { toast.style.display = 'none'; }, 4000);
        }
      } catch (err) {
        alert('Kunde inte spara inställningar: ' + err);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Spara och Tillämpa';
      }
    }
  </script>
</body>
</html>"""

# ==============================================================================
# FLASK ROUTER
# ==============================================================================
@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        data = request.get_json() or {}
        cfg = load_config()
        for k in ["dexcom_username", "dexcom_password", "dexcom_region", "hub_name", "hub_ip"]:
            if k in data:
                cfg[k] = data[k]
        save_config(cfg)
        print("[Config] Inställningar sparade via webbgränssnittet.", flush=True)
        return jsonify({"status": "ok"})
    else:
        cfg = load_config()
        local_ip = get_local_ip()
        return render_template_string(CONFIG_HTML, config=cfg, local_ip=local_ip)

@app.route("/data.json")
@app.route("/api/data")
def api_data():
    data = load_data()
    if data:
        return jsonify(data)
    return jsonify({"value": "--", "trend_symbol": "", "delta": "±0.0", "minutes_ago_text": "--", "history": []})

@app.route("/api/test-dexcom", methods=["POST"])
def api_test_dexcom():
    req = request.get_json() or {}
    username = req.get("username", "").strip()
    password = req.get("password", "").strip()
    region = req.get("region", "ous").strip()

    if not username or not password:
        return jsonify({"success": False, "error": "Både användarnamn och lösenord måste fyllas i."}), 400

    try:
        test_client = Dexcom(username=username, password=password, region=region)
        reading = test_client.get_current_glucose_reading()
        if reading:
            val = round(reading.mmol_l, 1) if hasattr(reading, "mmol_l") else round(reading.value / 18.0182, 1)
            arrow = get_trend_arrow(reading)
            trend_desc = reading.trend_description or ""
            time_str = reading.datetime.strftime("%H:%M")
            return jsonify({
                "success": True,
                "value": val,
                "arrow": arrow,
                "trend": trend_desc,
                "time": time_str
            })
        else:
            return jsonify({
                "success": True,
                "value": None,
                "message": "Inloggningen lyckades, men inget mätvärde fanns tillgängligt just nu."
            })
    except Exception as e:
        err_msg = str(e)
        if any(w.lower() in err_msg.lower() for w in ["accountpasswordinvalid", "usernotfound", "invalid credentials", "failed to authenticate", "401", "400"]):
            err_msg = "Felaktigt användarnamn eller lösenord. Kontrollera dina uppgifter och vald region."
        return jsonify({"success": False, "error": err_msg}), 400

@app.route("/api/discover-cast")
def api_discover_cast():
    refresh = request.args.get("refresh", "0") == "1"
    if refresh:
        t = threading.Thread(target=run_cast_discovery, daemon=True)
        t.start()
        t.join(timeout=3.5)
    with cast_discovery_lock:
        return jsonify({
            "devices": list(discovered_cast_devices),
            "scanning": is_discovering
        })

@app.route("/silence.wav")
def silence_wav():
    return Response(SILENT_WAV_BYTES, mimetype="audio/wav")


# ==============================================================================
# HUVUDPROGRAM
# ==============================================================================
def main():
    cfg = load_config()
    port = int(cfg.get("server_port", 8080))
    local_ip = get_local_ip()

    print("=" * 65, flush=True)
    print("   DEXCOM WEBSERVER – ALL-IN-ONE BLODSOCKERSYSTEM", flush=True)
    print("=" * 65, flush=True)
    print(f" * Webbadress (Display):      http://{local_ip}:{port}/", flush=True)
    print(f" * Inställningssida:          http://{local_ip}:{port}/config", flush=True)
    hub_display = f"'{cfg.get('hub_name')}' ({cfg.get('hub_ip')})" if (cfg.get('hub_name') or cfg.get('hub_ip')) else "Ingen enhet vald (gå till /config)"
    print(f" * Castar till enhet:         {hub_display}", flush=True)
    print("=" * 65, flush=True)

    # Starta Cast Discovery i bakgrunden direkt så enheter finns redo i /config
    t_discovery = threading.Thread(target=run_cast_discovery, daemon=True, name="InitialCastDiscovery")
    t_discovery.start()

    # Starta Dexcom-bakgrundstråd
    t_dexcom = threading.Thread(target=dexcom_worker, daemon=True, name="DexcomFetcher")
    t_dexcom.start()

    # Starta Cast-bakgrundstråd
    t_cast = threading.Thread(target=cast_worker, daemon=True, name="CastKeepAlive")
    t_cast.start()

    # Starta Flask webbserver
    # Använd Werkzeug WSGI server på port 8080 (lyssna på alla nätverkskort 0.0.0.0)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
