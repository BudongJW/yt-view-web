"""
YouTube View Web — 웹 대시보드 기반 YouTube 뷰어 봇
MShawon/YouTube-Viewer 핵심 로직 활용 + Flask 웹 UI
"""
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from random import choice, choices, randint, uniform
from time import gmtime, sleep, strftime

import psutil
import requests
from fake_headers import Headers
from faker import Faker
from flask import Flask, jsonify, render_template, request
from requests.exceptions import RequestException
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from undetected_chromedriver.patcher import Patcher
    HAS_PATCHER = True
except ImportError:
    HAS_PATCHER = False

fake = Faker()

# ── Global State ──
bot_state = {
    "running": False,
    "views": 0,
    "target_views": 100,
    "threads": 2,
    "logs": [],
    "errors": 0,
    "good_proxies": 0,
    "bad_proxies": 0,
    "start_time": None,
    "video_stats": {},
    "workers": {},
}

cancel_flag = threading.Event()

PROXY_DIR = os.path.join(os.path.dirname(__file__), "proxies")
os.makedirs(PROXY_DIR, exist_ok=True)

# ── Logging ──
def add_log(msg, level="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = {"time": ts, "msg": msg, "level": level}
    bot_state["logs"].insert(0, entry)
    if len(bot_state["logs"]) > 500:
        bot_state["logs"] = bot_state["logs"][:500]
    try:
        print(f"[{ts}] {msg}")
    except UnicodeEncodeError:
        print(f"[{ts}] {msg.encode('ascii', 'replace').decode()}")


# ── Proxy Fetcher ──
FREE_PROXY_SOURCES = [
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]

SOCKS5_SOURCES = [
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
]


def fetch_free_proxies(proxy_type="http"):
    sources = FREE_PROXY_SOURCES if proxy_type == "http" else SOCKS5_SOURCES
    proxies = set()
    for url in sources:
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                lines = resp.text.strip().split("\n")
                for line in lines:
                    line = line.strip()
                    if line and ":" in line:
                        proxies.add(line)
        except Exception as e:
            add_log(f"프록시 소스 실패: {url} | {e}", "warn")
    add_log(f"무료 {proxy_type} 프록시 {len(proxies)}개 수집 완료")
    return list(proxies)


def check_proxy(proxy, proxy_type="http", timeout=5):
    try:
        # Strip protocol prefix if present
        clean = proxy
        for prefix in ["http://", "https://", "socks5://", "socks4://"]:
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
        proxy_dict = {
            "http": f"{proxy_type}://{clean}",
            "https": f"{proxy_type}://{clean}",
        }
        resp = requests.get(
            "http://ip-api.com/json?fields=status",
            proxies=proxy_dict,
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception:
        return False


def pre_validate_proxies(proxy_list, proxy_type="http", max_workers=30, target=20):
    """Validate proxies in parallel using threads directly (avoid nested ThreadPoolExecutor on Windows)."""
    import random
    valid = []
    lock = threading.Lock()
    done_event = threading.Event()

    add_log(f"프록시 사전 검증 시작 (후보 {len(proxy_list)}개, 목표 {target}개)...")

    sample = random.sample(proxy_list, min(200, len(proxy_list)))

    def _check(proxy):
        if done_event.is_set() or cancel_flag.is_set():
            return
        if check_proxy(proxy, proxy_type, 5):
            with lock:
                valid.append(proxy)
                add_log(f"  Valid proxy: {proxy}", "success")
                if len(valid) >= target:
                    done_event.set()

    threads_list = []
    for p in sample:
        if done_event.is_set():
            break
        t = threading.Thread(target=_check, args=(p,), daemon=True)
        threads_list.append(t)
        t.start()
        # Limit concurrent threads
        while sum(1 for t in threads_list if t.is_alive()) >= max_workers:
            sleep(0.1)

    # Wait for remaining threads (max 30s)
    deadline = time.time() + 30
    for t in threads_list:
        remaining = max(0, deadline - time.time())
        t.join(timeout=remaining)
        if time.time() >= deadline or done_event.is_set():
            break

    add_log(f"프록시 검증 완료: {len(valid)}개 유효", "success" if valid else "error")
    return valid


# ── Chrome Driver ──
VIEWPORTS = [
    "2560,1440", "1920,1080", "1440,900",
    "1536,864", "1366,768", "1280,1024", "1024,768",
]

REFERERS = [
    "https://www.google.com/",
    "https://search.yahoo.com/",
    "https://duckduckgo.com/",
    "https://www.bing.com/",
    "https://t.co/",
    "",
]


CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.178 Safari/537.36"


def get_driver(background, agent, proxy=None, proxy_type="http"):
    options = webdriver.ChromeOptions()
    if background:
        options.add_argument("--headless=new")
    options.add_argument(f"--window-size={choice(VIEWPORTS)}")
    options.add_argument("--log-level=3")
    options.add_experimental_option(
        "excludeSwitches", ["enable-automation", "enable-logging"]
    )
    options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "intl.accept_languages": "en_US,en",
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,
        "download_restrictions": 3,
    }
    options.add_experimental_option("prefs", prefs)
    # Use current Chrome version UA to avoid YouTube supported_browsers redirect
    options.add_argument(f"user-agent={CHROME_UA}")
    options.add_argument("--mute-audio")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-features=UserAgentClientHint")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-blink-features=AutomationControlled")

    if proxy:
        options.add_argument(f"--proxy-server={proxy_type}://{proxy}")

    driver = webdriver.Chrome(options=options)
    return driver


def bypass_consent(driver):
    try:
        btns = driver.find_elements(
            By.CSS_SELECTOR, 'button[aria-label*="Accept"], button[aria-label*="agree"], #yDmH0d button'
        )
        for btn in btns:
            try:
                btn.click()
                sleep(1)
                return
            except Exception:
                continue
        # form submit fallback
        forms = driver.find_elements(By.CSS_SELECTOR, "form")
        for form in forms:
            try:
                form.submit()
                sleep(1)
                return
            except Exception:
                continue
    except Exception:
        pass


def play_video(driver):
    try:
        driver.find_element(By.CSS_SELECTOR, '[title^="Pause (k)"]')
    except WebDriverException:
        try:
            driver.find_element(
                By.CSS_SELECTOR, "button.ytp-large-play-button.ytp-button"
            ).send_keys(Keys.ENTER)
        except WebDriverException:
            try:
                driver.find_element(
                    By.CSS_SELECTOR, '[title^="Play (k)"]'
                ).click()
            except WebDriverException:
                try:
                    driver.execute_script(
                        "document.querySelector('button.ytp-play-button.ytp-button').click()"
                    )
                except WebDriverException:
                    pass


def save_bandwidth(driver):
    try:
        driver.find_element(By.CSS_SELECTOR, "button.ytp-settings-button").click()
        sleep(0.5)
        items = driver.find_elements(By.CSS_SELECTOR, ".ytp-menuitem")
        for item in items:
            if "Quality" in item.text or "화질" in item.text:
                item.click()
                sleep(0.5)
                qualities = driver.find_elements(By.CSS_SELECTOR, ".ytp-menuitem")
                if qualities:
                    qualities[-1].click()  # lowest quality
                break
    except Exception:
        pass


# ── Main Worker ──
def worker_view(position, proxy, proxy_type, config):
    if cancel_flag.is_set():
        return

    driver = None
    try:
        header = Headers(browser="chrome", os="win", headers=False).generate()
        agent = header["User-Agent"]

        is_direct = (proxy == "__direct__")

        if not is_direct:
            # Strip protocol prefix if present
            for prefix in ["http://", "https://", "socks5://", "socks4://"]:
                if proxy.startswith(prefix):
                    proxy = proxy[len(prefix):]

        bot_state["good_proxies"] += 1
        add_log(f"Worker {position} | {'Direct(no proxy)' if is_direct else proxy} | 드라이버 시작")

        background = config.get("headless", True)
        driver = get_driver(background, agent, None if is_direct else proxy, proxy_type)

        bot_state["workers"][position] = {"proxy": proxy, "status": "loading"}

        # Spoof timezone
        try:
            if is_direct:
                geo = requests.get("http://ip-api.com/json", timeout=10).json()
            else:
                proxy_dict = {"http": f"{proxy_type}://{proxy}", "https": f"{proxy_type}://{proxy}"}
                geo = requests.get("http://ip-api.com/json", proxies=proxy_dict, timeout=10).json()
            driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": geo.get("timezone", "UTC")})
            driver.execute_cdp_cmd("Emulation.setGeolocationOverride", {
                "latitude": geo.get("lat", 0),
                "longitude": geo.get("lon", 0),
                "accuracy": randint(20, 100),
            })
        except Exception:
            pass

        url = config["url"]

        # Navigate to video
        driver.get(url)
        sleep(3)

        # Bypass consent if redirected
        if "consent" in driver.current_url:
            bypass_consent(driver)
            sleep(2)
            if "consent" in driver.current_url:
                # Fallback: navigate directly again
                driver.get(url)
                sleep(3)

        # Wait for player with longer timeout
        try:
            WebDriverWait(driver, 45).until(
                EC.presence_of_element_located((By.ID, "movie_player"))
            )
            sleep(3)  # Extra settle time
        except Exception:
            page_title = driver.title
            page_url = driver.current_url
            raise Exception(f"비디오 플레이어 로드 실패 | title={page_title} | url={page_url}")

        # Save bandwidth
        if config.get("save_bandwidth", True):
            save_bandwidth(driver)

        play_video(driver)

        # Change playback speed
        speed = config.get("playback_speed", 1)
        if speed != 1:
            try:
                driver.execute_script(
                    f"document.querySelector('video').playbackRate = {speed};"
                )
            except Exception:
                pass

        # Get video duration
        video_len = 0
        for _ in range(30):
            video_len = driver.execute_script(
                "return document.getElementById('movie_player').getDuration()"
            )
            if video_len:
                break
            sleep(1)

        if not video_len:
            raise Exception("영상 길이를 가져올 수 없음")

        title = driver.title.replace(" - YouTube", "")
        min_pct = config.get("min_duration", 70) / 100
        max_pct = config.get("max_duration", 95) / 100
        watch_time = video_len * uniform(min_pct, max_pct)
        duration_str = strftime("%Mm:%Ss", gmtime(watch_time))

        bot_state["workers"][position] = {
            "proxy": proxy,
            "status": "watching",
            "title": title,
            "duration": duration_str,
        }

        add_log(f"Worker {position} | 시청 중: {title} | {duration_str}")

        # Watch loop
        loop_count = int(watch_time / 5)
        for i in range(loop_count):
            if cancel_flag.is_set():
                break
            sleep(5)
            try:
                current_time = driver.execute_script(
                    "return document.getElementById('movie_player').getCurrentTime()"
                )
                state = driver.execute_script(
                    "return document.getElementById('movie_player').getPlayerState()"
                )
                if state in [-1, 0]:
                    break
                if state == 2:  # paused
                    play_video(driver)
                if current_time >= watch_time:
                    break
            except Exception:
                break

        # Count view
        bot_state["views"] += 1
        title_short = title[:50]
        bot_state["video_stats"][title_short] = bot_state["video_stats"].get(title_short, 0) + 1
        add_log(f"Worker {position} | 조회 완료! 총 {bot_state['views']}회", "success")

    except Exception as e:
        bot_state["errors"] += 1
        add_log(f"Worker {position} | 오류: {e}", "error")
    finally:
        bot_state["workers"].pop(position, None)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_bot(config):
    cancel_flag.clear()
    bot_state["running"] = True
    bot_state["views"] = 0
    bot_state["errors"] = 0
    bot_state["good_proxies"] = 0
    bot_state["bad_proxies"] = 0
    bot_state["logs"] = []
    bot_state["video_stats"] = {}
    bot_state["workers"] = {}
    bot_state["start_time"] = time.time()
    bot_state["target_views"] = config.get("target_views", 100)

    add_log("봇 시작 - 프록시 수집 중...")

    proxy_type = config.get("proxy_type", "http")
    use_no_proxy = config.get("proxy_source") == "none"

    if use_no_proxy:
        # No proxy mode - use local IP directly
        proxy_list = ["__direct__"]
        add_log("프록시 없이 로컬 IP로 직접 접속 모드")
    elif config.get("proxy_source") == "custom" and config.get("custom_proxies"):
        proxy_list = [p.strip() for p in config["custom_proxies"].split("\n") if p.strip()]
        add_log(f"사용자 프록시 {len(proxy_list)}개 로드")
    else:
        raw_proxies = fetch_free_proxies(proxy_type)
        if not raw_proxies:
            add_log("프록시를 가져올 수 없습니다!", "error")
            bot_state["running"] = False
            return
        # Pre-validate in parallel
        target_valid = max(config.get("target_views", 100) * 2, 20)
        proxy_list = pre_validate_proxies(raw_proxies, proxy_type, max_workers=50, target=min(target_valid, 100))

    if not proxy_list:
        add_log("유효한 프록시가 없습니다!", "error")
        bot_state["running"] = False
        return

    target = config.get("target_views", 100)
    max_threads = config.get("threads", 3)
    if use_no_proxy:
        max_threads = 1  # Only 1 thread without proxy

    add_log(f"목표: {target}회 | 스레드: {max_threads} | 프록시: {'없음(직접)' if use_no_proxy else f'{len(proxy_list)}개'}")

    position = 0
    while bot_state["views"] < target and not cancel_flag.is_set():
        batch_size = min(max_threads, target - bot_state["views"])
        if not use_no_proxy:
            batch_size = min(batch_size, len(proxy_list))
        if batch_size <= 0:
            if use_no_proxy:
                break
            proxy_list = fetch_free_proxies(proxy_type)
            if not proxy_list:
                break
            continue

        batch_proxies = []
        for _ in range(batch_size):
            batch_proxies.append(choice(proxy_list))

        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = []
            for i, proxy in enumerate(batch_proxies):
                pos = position + i
                futures.append(executor.submit(worker_view, pos, proxy, proxy_type, config))
            wait(futures)

        position += batch_size

        if bot_state["views"] >= target:
            break

        # Small delay between batches
        sleep(2)

    add_log(f"봇 종료 - 총 조회수: {bot_state['views']}", "success")
    bot_state["running"] = False


# ── Flask App ──
app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def api_start():
    if bot_state["running"]:
        return jsonify({"error": "이미 실행 중입니다"}), 400

    data = request.json
    config = {
        "url": data.get("url", ""),
        "target_views": int(data.get("target_views", 50)),
        "threads": int(data.get("threads", 3)),
        "headless": data.get("headless", True),
        "save_bandwidth": data.get("save_bandwidth", True),
        "min_duration": int(data.get("min_duration", 70)),
        "max_duration": int(data.get("max_duration", 95)),
        "proxy_type": data.get("proxy_type", "http"),
        "proxy_source": data.get("proxy_source", "auto"),
        "custom_proxies": data.get("custom_proxies", ""),
        "playback_speed": float(data.get("playback_speed", 1)),
    }

    if not config["url"]:
        return jsonify({"error": "YouTube URL을 입력하세요"}), 400

    thread = threading.Thread(target=run_bot, args=(config,), daemon=True)
    thread.start()

    return jsonify({"status": "started"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    cancel_flag.set()
    bot_state["running"] = False
    add_log("사용자에 의해 봇 중지됨", "warn")
    return jsonify({"status": "stopped"})


@app.route("/api/status")
def api_status():
    elapsed = 0
    if bot_state["start_time"]:
        elapsed = int(time.time() - bot_state["start_time"])

    return jsonify({
        "running": bot_state["running"],
        "views": bot_state["views"],
        "target_views": bot_state["target_views"],
        "errors": bot_state["errors"],
        "good_proxies": bot_state["good_proxies"],
        "bad_proxies": bot_state["bad_proxies"],
        "elapsed": elapsed,
        "logs": bot_state["logs"][:100],
        "video_stats": bot_state["video_stats"],
        "workers": bot_state["workers"],
    })


@app.route("/api/fetch-proxies", methods=["POST"])
def api_fetch_proxies():
    data = request.json or {}
    proxy_type = data.get("proxy_type", "http")
    proxies = fetch_free_proxies(proxy_type)
    return jsonify({"count": len(proxies), "proxies": proxies[:50]})


@app.route("/api/validate-url", methods=["POST"])
def api_validate_url():
    """Validate YouTube URL and return video metadata (title, thumbnail, duration, views)."""
    data = request.json or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"valid": False, "error": "URL을 입력하세요"})

    # Extract video ID
    import re as _re
    vid_match = _re.search(r"(?:v=|youtu\.be/|shorts/)([0-9A-Za-z_-]{11})", url)
    if not vid_match:
        return jsonify({"valid": False, "error": "유효한 YouTube URL이 아닙니다"})

    video_id = vid_match.group(1)

    try:
        # Fetch oembed data (no API key needed)
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = requests.get(oembed_url, timeout=10)
        if resp.status_code != 200:
            return jsonify({"valid": False, "error": "영상을 찾을 수 없습니다 (비공개 또는 삭제됨)"})

        oembed = resp.json()
        title = oembed.get("title", "Unknown")
        author = oembed.get("author_name", "Unknown")
        thumbnail = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

        # Try to get additional info from page
        page_resp = requests.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": CHROME_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=10,
        )
        view_count = ""
        duration_text = ""
        if page_resp.status_code == 200:
            vc_match = _re.search(r'"viewCount"\s*:\s*"(\d+)"', page_resp.text)
            if vc_match:
                view_count = f"{int(vc_match.group(1)):,}"
            dur_match = _re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', page_resp.text)
            if dur_match:
                secs = int(dur_match.group(1))
                duration_text = strftime("%M:%S", gmtime(secs)) if secs < 3600 else strftime("%H:%M:%S", gmtime(secs))

        return jsonify({
            "valid": True,
            "video_id": video_id,
            "title": title,
            "author": author,
            "thumbnail": thumbnail,
            "view_count": view_count,
            "duration": duration_text,
        })

    except Exception as e:
        return jsonify({"valid": False, "error": f"검증 실패: {str(e)}"})


@app.route("/api/verify-result", methods=["POST"])
def api_verify_result():
    """After bot run, re-fetch video info to check if view count changed."""
    data = request.json or {}
    url = data.get("url", "").strip()

    import re as _re
    vid_match = _re.search(r"(?:v=|youtu\.be/|shorts/)([0-9A-Za-z_-]{11})", url)
    if not vid_match:
        return jsonify({"error": "유효한 URL 아님"})

    video_id = vid_match.group(1)

    try:
        page_resp = requests.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": CHROME_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=10,
        )
        view_count = 0
        view_text = ""
        if page_resp.status_code == 200:
            vc_match = _re.search(r'"viewCount"\s*:\s*"(\d+)"', page_resp.text)
            if vc_match:
                view_count = int(vc_match.group(1))
                view_text = f"{view_count:,}"

        return jsonify({
            "video_id": video_id,
            "current_views": view_count,
            "current_views_text": view_text,
            "bot_views": bot_state["views"],
            "bot_errors": bot_state["errors"],
            "bot_elapsed": int(time.time() - bot_state["start_time"]) if bot_state["start_time"] else 0,
            "video_stats": bot_state["video_stats"],
        })
    except Exception as e:
        return jsonify({"error": f"검증 실패: {str(e)}"})


@app.route("/api/logs")
def api_logs():
    """Return full logs with filtering support."""
    level = request.args.get("level", "all")
    limit = int(request.args.get("limit", 200))

    logs = bot_state["logs"]
    if level != "all":
        logs = [l for l in logs if l["level"] == level]

    return jsonify({
        "logs": logs[:limit],
        "total": len(bot_state["logs"]),
        "counts": {
            "info": sum(1 for l in bot_state["logs"] if l["level"] == "info"),
            "success": sum(1 for l in bot_state["logs"] if l["level"] == "success"),
            "error": sum(1 for l in bot_state["logs"] if l["level"] == "error"),
            "warn": sum(1 for l in bot_state["logs"] if l["level"] == "warn"),
        },
    })


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  YouTube View Web Dashboard")
    print("  http://127.0.0.1:5000")
    print("=" * 50 + "\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
