"""
YouTube View Web — 웹 대시보드 기반 YouTube 뷰어 봇
MShawon/YouTube-Viewer 핵심 로직 활용 + Flask 웹 UI
"""
import json
import os
import shutil
import threading
import time
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
PROXY_SOURCES = {
    "http": [
        # === GitHub raw lists (auto-updated) ===
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies.txt",
        "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/http.txt",
        "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
        "https://raw.githubusercontent.com/ClearProxy/checked-proxy-list/main/http/raw/all.txt",
        "https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt",
        "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS.txt",
        "https://raw.githubusercontent.com/shiftytr/proxy-list/master/proxy.txt",
        # === API endpoints ===
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    ],
    "socks4": [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
        "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/socks4.txt",
        "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/socks4.txt",
        "https://raw.githubusercontent.com/ClearProxy/checked-proxy-list/main/socks4/raw/all.txt",
        "https://raw.githubusercontent.com/prxchk/proxy-list/main/socks4.txt",
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=10000&country=all",
    ],
    "socks5": [
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/socks5.txt",
        "https://raw.githubusercontent.com/ClearProxy/checked-proxy-list/main/socks5/raw/all.txt",
        "https://raw.githubusercontent.com/prxchk/proxy-list/main/socks5.txt",
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=10000&country=all",
    ],
}


def fetch_free_proxies(proxy_type="http"):
    sources = PROXY_SOURCES.get(proxy_type, PROXY_SOURCES["http"])
    proxies = set()
    fetch_lock = threading.Lock()
    source_results = []

    def _fetch_one(url):
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                found = set()
                for line in resp.text.strip().split("\n"):
                    line = line.strip()
                    if line and ":" in line and not line.startswith("#"):
                        # Strip protocol prefix if present
                        clean = strip_proxy_prefix(line)
                        if clean and ":" in clean:
                            found.add(clean)
                with fetch_lock:
                    proxies.update(found)
                    source_results.append((url.split("/")[-2] if "github" in url else url.split("/")[2], len(found)))
        except Exception:
            pass

    # Fetch all sources in parallel
    threads = []
    for url in sources:
        t = threading.Thread(target=_fetch_one, args=(url,), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=20)

    # Log results
    for src, cnt in sorted(source_results, key=lambda x: -x[1]):
        add_log(f"  {src}: {cnt}개", "info")
    add_log(f"무료 {proxy_type} 프록시 {len(proxies)}개 수집 완료 ({len(source_results)}/{len(sources)} 소스)")
    return list(proxies)


def strip_proxy_prefix(proxy):
    """Remove protocol prefix from proxy string."""
    for prefix in ["http://", "https://", "socks5://", "socks4://"]:
        if proxy.startswith(prefix):
            return proxy[len(prefix):]
    return proxy


def check_proxy_youtube(proxy, proxy_type="http", timeout=8):
    """Validate proxy by actually hitting YouTube (more reliable than ip-api)."""
    try:
        clean = strip_proxy_prefix(proxy)
        proxy_dict = {
            "http": f"{proxy_type}://{clean}",
            "https": f"{proxy_type}://{clean}",
        }
        resp = requests.get(
            "https://www.youtube.com/robots.txt",
            proxies=proxy_dict,
            timeout=timeout,
            headers={"User-Agent": CHROME_UA},
        )
        return resp.status_code == 200 and "Disallow" in resp.text
    except Exception:
        return False



# Shared bad proxy set to avoid reuse across batches
_bad_proxy_set = set()
_bad_proxy_lock = threading.Lock()


def mark_bad_proxy(proxy):
    with _bad_proxy_lock:
        _bad_proxy_set.add(strip_proxy_prefix(proxy))


def is_bad_proxy(proxy):
    with _bad_proxy_lock:
        return strip_proxy_prefix(proxy) in _bad_proxy_set


def pre_validate_proxies(proxy_list, proxy_type="http", max_workers=150, target=30):
    """Direct YouTube reachability validation with high concurrency.
    Prioritizes accuracy over speed — tests proxies directly against YouTube."""
    import random
    final_valid = []
    lock = threading.Lock()
    done_event = threading.Event()

    # ~1% pass rate observed, so sample target * 150 to be safe
    needed_samples = min(len(proxy_list), max(target * 150, 3000))
    sample = random.sample(proxy_list, needed_samples)

    add_log(f"프록시 검증 시작 (후보 {len(proxy_list)}개, 테스트 {len(sample)}개, 목표 {target}개)...")
    add_log(f"YouTube 직접 검증 (동시 {max_workers} 스레드)...")

    tested = [0]  # mutable counter

    def _yt_check(proxy):
        if done_event.is_set() or cancel_flag.is_set():
            return
        if check_proxy_youtube(proxy, proxy_type, 12):
            with lock:
                final_valid.append(proxy)
                add_log(f"  YouTube OK: {strip_proxy_prefix(proxy)} [{len(final_valid)}/{target}]", "success")
                if len(final_valid) >= target:
                    done_event.set()
        with lock:
            tested[0] += 1
            # Progress update every 500 proxies
            if tested[0] % 500 == 0:
                add_log(f"  검증 진행: {tested[0]}/{len(sample)} 테스트, {len(final_valid)}개 유효")

    threads_list = []
    for p in sample:
        if done_event.is_set() or cancel_flag.is_set():
            break
        t = threading.Thread(target=_yt_check, args=(p,), daemon=True)
        threads_list.append(t)
        t.start()
        while sum(1 for t in threads_list if t.is_alive()) >= max_workers:
            sleep(0.01)

    # Allow enough time: 12s timeout * 2 buffer
    deadline = time.time() + 180
    for t in threads_list:
        if done_event.is_set():
            break
        remaining = max(0, deadline - time.time())
        t.join(timeout=remaining)
        if time.time() >= deadline:
            break

    add_log(
        f"검증 완료: {len(final_valid)}개 YouTube 유효 프록시 (테스트 {tested[0]}/{len(sample)})",
        "success" if final_valid else "error",
    )
    return final_valid


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
def _try_load_video(driver, config, position, proxy_label):
    """Navigate to YouTube video and wait for player. Returns True on success."""
    url = config["url"]

    driver.get(url)
    sleep(4)

    # Handle supported_browsers redirect
    if "supported_browsers" in driver.current_url:
        add_log(f"Worker {position} | supported_browsers redirect - retrying", "warn")
        driver.get(url)
        sleep(4)

    # Bypass consent if redirected
    if "consent" in driver.current_url:
        bypass_consent(driver)
        sleep(2)
        if "consent" in driver.current_url:
            driver.get(url)
            sleep(4)

    # Wait for player
    try:
        WebDriverWait(driver, 40).until(
            EC.presence_of_element_located((By.ID, "movie_player"))
        )
        sleep(3)
        return True
    except Exception:
        cur_url = driver.current_url
        if "supported_browsers" in cur_url:
            add_log(f"Worker {position} | {proxy_label} | YouTube blocked (unsupported browser)", "warn")
        else:
            add_log(f"Worker {position} | {proxy_label} | Player load failed | url={cur_url[:80]}", "warn")
        return False


def _watch_video(driver, config, position, proxy_label):
    """Play and watch the video. Returns True if view was counted."""
    # Save bandwidth
    if config.get("save_bandwidth", True):
        save_bandwidth(driver)

    play_video(driver)

    # Change playback speed
    speed = config.get("playback_speed", 1)
    if speed != 1:
        try:
            driver.execute_script(f"document.querySelector('video').playbackRate = {speed};")
        except Exception:
            pass

    # Get video duration
    video_len = 0
    for _ in range(20):
        video_len = driver.execute_script(
            "return document.getElementById('movie_player').getDuration()"
        )
        if video_len:
            break
        sleep(1)

    if not video_len:
        raise Exception("Cannot get video duration")

    title = driver.title.replace(" - YouTube", "")
    min_pct = config.get("min_duration", 70) / 100
    max_pct = config.get("max_duration", 95) / 100
    watch_time = video_len * uniform(min_pct, max_pct)
    duration_str = strftime("%Mm:%Ss", gmtime(watch_time))

    bot_state["workers"][position] = {
        "proxy": proxy_label,
        "status": "watching",
        "title": title,
        "duration": duration_str,
    }

    add_log(f"Worker {position} | {proxy_label} | {title} | {duration_str}")

    # Watch loop
    error_streak = 0
    loop_count = int(watch_time / 5)
    for _ in range(loop_count):
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
            if state in [-1, 0]:  # unstarted or ended
                break
            if state == 2:  # paused
                play_video(driver)
            if state == 3:  # buffering
                error_streak += 1
                if error_streak > 6:
                    raise Exception("Buffering too long")
            else:
                error_streak = 0
            if current_time >= watch_time:
                break
        except WebDriverException:
            error_streak += 1
            if error_streak > 4:
                raise Exception("Player communication lost")

    # Count view
    bot_state["views"] += 1
    title_short = title[:50]
    bot_state["video_stats"][title_short] = bot_state["video_stats"].get(title_short, 0) + 1
    add_log(f"Worker {position} | View #{bot_state['views']} OK", "success")
    return True


def worker_view(position, proxy, proxy_type, config):
    """Worker with retry logic: tries up to MAX_RETRIES different proxies on failure."""
    if cancel_flag.is_set():
        return

    is_direct = (proxy == "__direct__")
    max_retries = 1 if is_direct else config.get("_max_retries", 3)
    proxy_pool = config.get("_proxy_pool", [proxy])

    for attempt in range(max_retries):
        if cancel_flag.is_set():
            return

        # Pick proxy (rotate on retry)
        if attempt > 0 and not is_direct:
            available = [p for p in proxy_pool if not is_bad_proxy(p)]
            if not available:
                add_log(f"Worker {position} | No more proxies to try", "error")
                break
            proxy = choice(available)

        clean_proxy = proxy if is_direct else strip_proxy_prefix(proxy)
        proxy_label = "Direct" if is_direct else clean_proxy

        driver = None
        try:
            if not is_direct and is_bad_proxy(proxy):
                continue

            bot_state["good_proxies"] += 1
            retry_tag = f" (retry {attempt})" if attempt > 0 else ""
            add_log(f"Worker {position} | {proxy_label}{retry_tag} | Starting driver")

            background = config.get("headless", True)
            driver = get_driver(background, None, None if is_direct else clean_proxy, proxy_type)

            bot_state["workers"][position] = {"proxy": proxy_label, "status": "loading"}

            # Spoof timezone (skip on retry to save time)
            if attempt == 0:
                try:
                    if is_direct:
                        geo = requests.get("http://ip-api.com/json", timeout=8).json()
                    else:
                        pdict = {"http": f"{proxy_type}://{clean_proxy}", "https": f"{proxy_type}://{clean_proxy}"}
                        geo = requests.get("http://ip-api.com/json", proxies=pdict, timeout=8).json()
                    driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": geo.get("timezone", "UTC")})
                    driver.execute_cdp_cmd("Emulation.setGeolocationOverride", {
                        "latitude": geo.get("lat", 0),
                        "longitude": geo.get("lon", 0),
                        "accuracy": randint(20, 100),
                    })
                except Exception:
                    pass

            # Try loading video
            if not _try_load_video(driver, config, position, proxy_label):
                if not is_direct:
                    mark_bad_proxy(proxy)
                    bot_state["bad_proxies"] += 1
                raise Exception("Video load failed")

            # Watch video
            _watch_video(driver, config, position, proxy_label)
            return  # Success, exit retry loop

        except Exception as e:
            err_msg = str(e)
            if len(err_msg) > 150:
                err_msg = err_msg[:150] + "..."
            if attempt == max_retries - 1:
                bot_state["errors"] += 1
                add_log(f"Worker {position} | FAIL after {attempt+1} tries: {err_msg}", "error")
            else:
                add_log(f"Worker {position} | Retry {attempt+1}: {err_msg}", "warn")
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

    # Clear bad proxy tracking from previous runs
    with _bad_proxy_lock:
        _bad_proxy_set.clear()

    add_log("봇 시작 - 프록시 수집 중...")

    proxy_type = config.get("proxy_type", "http")
    use_no_proxy = config.get("proxy_source") == "none"

    if use_no_proxy:
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
        target_valid = max(config.get("target_views", 100) * 2, 20)
        proxy_list = pre_validate_proxies(raw_proxies, proxy_type, max_workers=50, target=min(target_valid, 100))

    if not proxy_list:
        add_log("유효한 프록시가 없습니다!", "error")
        bot_state["running"] = False
        return

    # Pass full proxy pool and retry config to workers
    config["_proxy_pool"] = proxy_list
    config["_max_retries"] = 3

    target = config.get("target_views", 100)
    max_threads = config.get("threads", 3)
    if use_no_proxy:
        max_threads = 1

    add_log(f"목표: {target}회 | 스레드: {max_threads} | 프록시: {'없음(직접)' if use_no_proxy else f'{len(proxy_list)}개'}")

    refetch_count = 0
    position = 0
    while bot_state["views"] < target and not cancel_flag.is_set():
        # Filter out known-bad proxies for batch selection
        if not use_no_proxy:
            available = [p for p in proxy_list if not is_bad_proxy(p)]
            if len(available) < max_threads:
                add_log(f"사용 가능한 프록시 부족 ({len(available)}개) - 새 프록시 수집 중...", "warn")
                refetch_count += 1
                if refetch_count > 3:
                    add_log("프록시 재수집 한도 초과, 종료합니다", "error")
                    break
                raw_proxies = fetch_free_proxies(proxy_type)
                if raw_proxies:
                    new_proxies = pre_validate_proxies(raw_proxies, proxy_type, max_workers=50, target=50)
                    proxy_list.extend(new_proxies)
                    config["_proxy_pool"] = proxy_list
                    add_log(f"새 프록시 {len(new_proxies)}개 추가 (총 {len(proxy_list)}개)")
                available = [p for p in proxy_list if not is_bad_proxy(p)]
                if not available:
                    add_log("유효한 프록시가 모두 소진되었습니다", "error")
                    break
        else:
            available = proxy_list

        batch_size = min(max_threads, target - bot_state["views"], len(available))
        if batch_size <= 0:
            break

        batch_proxies = []
        for _ in range(batch_size):
            batch_proxies.append(choice(available))

        # Use raw threads instead of ThreadPoolExecutor (Windows compat)
        threads = []
        for i, proxy in enumerate(batch_proxies):
            pos = position + i
            t = threading.Thread(target=worker_view, args=(pos, proxy, proxy_type, config))
            t.daemon = True
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=300)

        position += batch_size

        if bot_state["views"] >= target:
            break

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
