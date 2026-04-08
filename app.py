"""
YouTube View Web — 웹 대시보드 기반 YouTube 뷰어 봇
zendriver (CDP) + 핑거프린트 스푸핑 + 트래픽 소스 다양화 + Shorts 지원
"""
import asyncio
import io
import json
import math
import os
import re
import sys
import threading
import time
from datetime import datetime
from random import choice, gauss, randint, random, sample, shuffle, uniform
from time import gmtime, sleep, strftime

import zendriver as uc
import requests
from flask import Flask, jsonify, render_template, request

# Fix Windows cp949 encoding crash (emoji/unicode in zendriver output)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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
                        clean = strip_proxy_prefix(line)
                        if clean and ":" in clean:
                            found.add(clean)
                with fetch_lock:
                    proxies.update(found)
                    source_results.append((url.split("/")[-2] if "github" in url else url.split("/")[2], len(found)))
        except Exception:
            pass

    threads = []
    for url in sources:
        t = threading.Thread(target=_fetch_one, args=(url,), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=20)

    for src, cnt in sorted(source_results, key=lambda x: -x[1]):
        add_log(f"  {src}: {cnt}개", "info")
    add_log(f"무료 {proxy_type} 프록시 {len(proxies)}개 수집 완료 ({len(source_results)}/{len(sources)} 소스)")
    return list(proxies)


def strip_proxy_prefix(proxy):
    for prefix in ["http://", "https://", "socks5://", "socks4://"]:
        if proxy.startswith(prefix):
            return proxy[len(prefix):]
    return proxy


CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.178 Safari/537.36"


def check_proxy_youtube(proxy, proxy_type="http", timeout=8):
    try:
        clean = strip_proxy_prefix(proxy)
        proxy_dict = {
            "http": f"{proxy_type}://{clean}",
            "https": f"{proxy_type}://{clean}",
        }
        resp = requests.get(
            "https://www.youtube.com/robots.txt",
            proxies=proxy_dict, timeout=timeout,
            headers={"User-Agent": CHROME_UA},
        )
        return resp.status_code == 200 and "Disallow" in resp.text
    except Exception:
        return False


_bad_proxy_set = set()
_bad_proxy_lock = threading.Lock()


def mark_bad_proxy(proxy):
    with _bad_proxy_lock:
        _bad_proxy_set.add(strip_proxy_prefix(proxy))


def is_bad_proxy(proxy):
    with _bad_proxy_lock:
        return strip_proxy_prefix(proxy) in _bad_proxy_set


def pre_validate_proxies(proxy_list, proxy_type="http", max_workers=150, target=30):
    import random as _rand
    final_valid = []
    lock = threading.Lock()
    done_event = threading.Event()

    needed_samples = min(len(proxy_list), max(target * 150, 3000))
    sample_list = _rand.sample(proxy_list, needed_samples)

    add_log(f"프록시 검증 시작 (후보 {len(proxy_list)}개, 테스트 {len(sample_list)}개, 목표 {target}개)...")
    add_log(f"YouTube 직접 검증 (동시 {max_workers} 스레드)...")

    tested = [0]

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
            if tested[0] % 500 == 0:
                add_log(f"  검증 진행: {tested[0]}/{len(sample_list)} 테스트, {len(final_valid)}개 유효")

    threads_list = []
    for p in sample_list:
        if done_event.is_set() or cancel_flag.is_set():
            break
        t = threading.Thread(target=_yt_check, args=(p,), daemon=True)
        threads_list.append(t)
        t.start()
        while sum(1 for t in threads_list if t.is_alive()) >= max_workers:
            sleep(0.01)

    deadline = time.time() + 180
    for t in threads_list:
        if done_event.is_set():
            break
        remaining = max(0, deadline - time.time())
        t.join(timeout=remaining)
        if time.time() >= deadline:
            break

    add_log(
        f"검증 완료: {len(final_valid)}개 YouTube 유효 프록시 (테스트 {tested[0]}/{len(sample_list)})",
        "success" if final_valid else "error",
    )
    return final_valid


# ── zendriver (CDP) Browser ──
VIEWPORTS = [
    (2560, 1440), (1920, 1080), (1440, 900),
    (1536, 864), (1366, 768), (1280, 1024), (1024, 768),
]

# Mobile UAs for Shorts
MOBILE_UAS = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.178 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.178 Mobile Safari/537.36",
]

# External referrers for traffic source diversity
EXTERNAL_REFERRERS = [
    "https://www.google.com/search?q=",
    "https://www.bing.com/search?q=",
    "https://search.yahoo.com/search?p=",
    "https://duckduckgo.com/?q=",
    "https://t.co/redirect?url=",
]

SEARCH_KEYWORDS = [
    "music video", "tutorial", "review", "funny moments", "vlog",
    "how to", "best of", "gameplay", "reaction", "documentary",
]


# ── Fingerprint Spoofing JS ──
FINGERPRINT_SPOOF_JS = '''
(() => {
    // Canvas fingerprint randomization
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        const ctx = this.getContext('2d');
        if (ctx) {
            const imgData = ctx.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < Math.min(imgData.data.length, 20); i += 4) {
                imgData.data[i] = imgData.data[i] ^ __CANVAS_SEED__;
            }
            ctx.putImageData(imgData, 0, 0);
        }
        return origToDataURL.apply(this, arguments);
    };

    // WebGL vendor/renderer spoofing
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
        if (param === 37445) return 'Google Inc. (NVIDIA)';
        if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX __GPU_MODEL__ Direct3D11 vs_5_0 ps_5_0)';
        return getParam.apply(this, arguments);
    };

    // WebRTC IP leak prevention
    if (window.RTCPeerConnection) {
        const origRTC = window.RTCPeerConnection;
        window.RTCPeerConnection = function(...args) {
            if (args[0] && args[0].iceServers) {
                args[0].iceServers = [];
            }
            return new origRTC(...args);
        };
        window.RTCPeerConnection.prototype = origRTC.prototype;
    }

    // AudioContext fingerprint defense
    const origGetFloatFreqData = AnalyserNode.prototype.getFloatFrequencyData;
    AnalyserNode.prototype.getFloatFrequencyData = function(array) {
        origGetFloatFreqData.apply(this, arguments);
        for (let i = 0; i < Math.min(array.length, 10); i++) {
            array[i] = array[i] + (__AUDIO_SEED__ * 0.00001);
        }
    };

    // Navigator properties
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => __HW_CONCURRENCY__ });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => __DEV_MEMORY__ });
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
})();
'''

GPU_MODELS = ["1060", "1070", "1080", "2060", "2070", "2080", "3060", "3070", "3080", "4060", "4070", "4080"]


def _make_fingerprint_js():
    """Generate unique fingerprint spoof JS per session."""
    js = FINGERPRINT_SPOOF_JS
    js = js.replace("__CANVAS_SEED__", str(randint(1, 255)))
    js = js.replace("__GPU_MODEL__", choice(GPU_MODELS))
    js = js.replace("__AUDIO_SEED__", str(randint(-50, 50)))
    js = js.replace("__HW_CONCURRENCY__", str(choice([2, 4, 8, 12, 16])))
    js = js.replace("__DEV_MEMORY__", str(choice([2, 4, 8, 16])))
    return js


# ── Human Behavior Simulation JS ──
HUMAN_BEHAVIOR_JS = '''
(() => {
    // Random scroll to comments area
    const scrollTarget = Math.random() * 800 + 200;
    window.scrollTo({ top: scrollTarget, behavior: 'smooth' });

    setTimeout(() => {
        // Scroll back up
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }, __SCROLL_BACK_DELAY__);
})();
'''


async def _simulate_human(page):
    """Simulate human-like interactions during video watch."""
    actions = ["scroll", "nothing", "nothing", "scroll_comments"]
    action = choice(actions)

    if action == "scroll":
        scroll_y = randint(100, 500)
        await page.evaluate(f"window.scrollTo({{top: {scroll_y}, behavior: 'smooth'}})")
        await page.sleep(_gaussian_delay(2, 0.5))
        await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    elif action == "scroll_comments":
        js = HUMAN_BEHAVIOR_JS.replace("__SCROLL_BACK_DELAY__", str(randint(2000, 5000)))
        await page.evaluate(js)


def _gaussian_delay(mean, stddev):
    """Return a gaussian-distributed delay (clamped to positive)."""
    return max(0.5, gauss(mean, stddev))


async def start_browser(headless=True, proxy=None, proxy_type="http", is_shorts=False):
    """Start a zendriver browser with optimized flags + anti-detect."""
    if is_shorts:
        vp = choice([(412, 915), (390, 844), (414, 896)])
        ua = choice(MOBILE_UAS)
    else:
        vp = choice(VIEWPORTS)
        ua = CHROME_UA

    args = [
        f"--window-size={vp[0]},{vp[1]}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--mute-audio",
        "--disable-features=UserAgentClientHint",
        "--blink-settings=imagesEnabled=false",
        "--js-flags=--max-old-space-size=128",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-webrtc-hw-encoding",
        "--disable-webrtc-hw-decoding",
        "--enforce-webrtc-ip-permission-check",
        f"--user-agent={ua}",
    ]
    if proxy:
        args.append(f"--proxy-server={proxy_type}://{proxy}")

    browser = await uc.start(headless=headless, browser_args=args)
    return browser


def _detect_content_type(url):
    """Detect if URL is Shorts or regular video."""
    if "/shorts/" in url:
        return "shorts"
    return "video"


def _extract_video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([0-9A-Za-z_-]{11})", url)
    return m.group(1) if m else None


def _build_navigation_url(url, traffic_source, video_id):
    """Build URL based on traffic source mode."""
    if traffic_source == "direct":
        return url
    elif traffic_source == "external":
        referrer = choice(EXTERNAL_REFERRERS)
        return url  # We'll set the referrer header instead
    elif traffic_source == "search":
        return "https://www.youtube.com"  # We'll search then click
    return url


async def _navigate_via_search(page, browser, video_id, position, proxy_label):
    """Navigate to video via YouTube search (mimics organic discovery)."""
    try:
        page = await browser.get("https://www.youtube.com")
        await page.sleep(_gaussian_delay(3, 0.8))

        # Type in search box
        search_box = await page.query_selector('input#search, input[name="search_query"]')
        if not search_box:
            add_log(f"Worker {position} | Search box not found, falling back to direct", "warn")
            return None

        # Type keyword + video ID (to find our specific video)
        keyword = choice(SEARCH_KEYWORDS)
        search_query = f"{keyword} {video_id}"

        await search_box.click()
        await page.sleep(_gaussian_delay(0.5, 0.2))

        # Type letter by letter with random delays
        for ch in search_query:
            await page.evaluate(f'''
                document.querySelector('input#search, input[name="search_query"]').value += '{ch}';
                document.querySelector('input#search, input[name="search_query"]').dispatchEvent(new Event('input', {{bubbles: true}}));
            ''')
            await page.sleep(uniform(0.05, 0.2))

        await page.sleep(_gaussian_delay(0.8, 0.3))

        # Press Enter to search
        await page.evaluate('''
            document.querySelector('form#search-form, form[action="/results"]').submit();
        ''')
        await page.sleep(_gaussian_delay(4, 1))

        # Try to find and click our video in results
        clicked = await page.evaluate(f'''
            (() => {{
                const links = document.querySelectorAll('a#video-title, ytd-video-renderer a');
                for (const link of links) {{
                    if (link.href && link.href.includes('{video_id}')) {{
                        link.click();
                        return true;
                    }}
                }}
                return false;
            }})()
        ''')

        if clicked:
            await page.sleep(_gaussian_delay(4, 1))
            return page
        else:
            add_log(f"Worker {position} | Video not found in search, direct fallback", "warn")
            return None

    except Exception as e:
        add_log(f"Worker {position} | Search navigation failed: {str(e)[:60]}", "warn")
        return None


async def _navigate_via_external(page, browser, url, position):
    """Navigate with external referrer (simulates clicking from Google/social)."""
    referrer = choice(EXTERNAL_REFERRERS)
    # Set referrer via CDP
    try:
        await page.evaluate(f'''
            Object.defineProperty(document, 'referrer', {{
                get: () => '{referrer}'
            }});
        ''')
    except Exception:
        pass
    page = await browser.get(url)
    return page


async def _try_load_video_async(page, browser, config, position, proxy_label):
    """Navigate to YouTube video with traffic source diversity."""
    url = config["url"]
    traffic_source = config.get("traffic_source", "mixed")
    video_id = _extract_video_id(url)
    content_type = config.get("_content_type", "video")

    # Pick traffic source for this attempt
    if traffic_source == "mixed":
        source = choice(["direct", "direct", "external", "search"])
    else:
        source = traffic_source

    # Inject fingerprint spoofing
    fp_js = _make_fingerprint_js()
    try:
        first_page = await browser.get("about:blank")
        await first_page.evaluate(fp_js)
    except Exception:
        pass

    # Navigate based on source
    if source == "search" and video_id and content_type != "shorts":
        result = await _navigate_via_search(page, browser, video_id, position, proxy_label)
        if result:
            page = result
        else:
            source = "direct"  # fallback

    if source == "external":
        page = await _navigate_via_external(page, browser, url, position)
        await page.sleep(_gaussian_delay(4, 1))
    elif source == "direct" or (source == "search" and content_type == "shorts"):
        page = await browser.get(url)
        await page.sleep(_gaussian_delay(4, 1))

    # Handle supported_browsers redirect
    cur_url = page.url or ""
    if "supported_browsers" in cur_url:
        add_log(f"Worker {position} | supported_browsers redirect - retrying", "warn")
        page = await browser.get(url)
        await page.sleep(4)
        cur_url = page.url or ""

    # Handle consent redirect
    if "consent" in cur_url:
        await page.evaluate('''
            (() => {
                const btns = document.querySelectorAll('button[aria-label*="Accept"], button[aria-label*="agree"], #yDmH0d button');
                if (btns.length > 0) btns[0].click();
                else { const forms = document.querySelectorAll('form'); if (forms.length > 0) forms[0].submit(); }
            })()
        ''')
        await page.sleep(3)
        cur_url = page.url or ""
        if "consent" in cur_url:
            page = await browser.get(url)
            await page.sleep(4)

    # For Shorts, check for shorts player
    if content_type == "shorts":
        for _ in range(15):
            has_shorts = await page.evaluate('''
                !!document.querySelector('ytd-shorts, ytd-reel-video-renderer, #shorts-player, video')
            ''')
            if has_shorts:
                await page.sleep(2)
                add_log(f"Worker {position} | {proxy_label} | Shorts loaded [src={source}]")
                return page
            await page.sleep(2)
    else:
        # Regular video: wait for player
        for _ in range(20):
            has_player = await page.evaluate('!!document.getElementById("movie_player")')
            if has_player:
                await page.sleep(3)
                add_log(f"Worker {position} | {proxy_label} | Player loaded [src={source}]")
                return page
            await page.sleep(2)

    cur_url = page.url or ""
    if "supported_browsers" in cur_url:
        add_log(f"Worker {position} | {proxy_label} | YouTube blocked (unsupported browser)", "warn")
    else:
        add_log(f"Worker {position} | {proxy_label} | Load failed | url={cur_url[:80]}", "warn")
    return None


async def _watch_shorts_async(page, config, position, proxy_label):
    """Watch a YouTube Short. Each play/replay = 1 view (no min watch time)."""
    loops = config.get("shorts_loops", 3)

    # Ensure video is playing
    await page.evaluate('''
        (() => {
            const v = document.querySelector('video');
            if (v) { v.play(); v.muted = true; }
        })()
    ''')
    await page.sleep(2)

    raw_title = await page.evaluate("document.title")
    title = (raw_title or "").replace(" - YouTube", "")

    bot_state["workers"][position] = {
        "proxy": proxy_label,
        "status": f"shorts x{loops}",
        "title": title[:60],
    }

    for loop_i in range(loops):
        if cancel_flag.is_set():
            break

        # Wait for video to finish or watch 3-15 seconds
        watch_sec = uniform(3, 15)
        await page.sleep(watch_sec)

        # Replay by seeking to 0
        await page.evaluate('''
            (() => {
                const v = document.querySelector('video');
                if (v) { v.currentTime = 0; v.play(); }
            })()
        ''')

        # Count each loop as a view
        bot_state["views"] += 1
        title_short = title[:50]
        bot_state["video_stats"][title_short] = bot_state["video_stats"].get(title_short, 0) + 1
        add_log(f"Worker {position} | Shorts view #{bot_state['views']} (loop {loop_i+1}/{loops})", "success")

        # Human behavior between loops
        if loop_i < loops - 1:
            await page.sleep(_gaussian_delay(1.5, 0.5))

    return True


async def _watch_video_async(page, config, position, proxy_label):
    """Play and watch regular video via CDP."""
    # Set lowest quality
    if config.get("save_bandwidth", True):
        await page.evaluate('''
            (() => {
                try {
                    const p = document.getElementById('movie_player');
                    if (p && p.setPlaybackQualityRange) p.setPlaybackQualityRange('tiny', 'tiny');
                    else if (p && p.setPlaybackQuality) p.setPlaybackQuality('tiny');
                } catch(e) {}
            })()
        ''')

    # Play video
    await page.evaluate('''
        (() => {
            const p = document.getElementById('movie_player');
            if (p && p.playVideo) p.playVideo();
        })()
    ''')

    speed = config.get("playback_speed", 1)
    if speed != 1:
        await page.evaluate(f"try {{ document.querySelector('video').playbackRate = {speed}; }} catch(e) {{}}")

    # Get video duration
    video_len = 0
    for _ in range(20):
        video_len = await page.evaluate(
            "(() => { try { return document.getElementById('movie_player').getDuration(); } catch(e) { return 0; } })()"
        )
        if video_len:
            break
        await page.sleep(1)

    if not video_len:
        raise Exception("Cannot get video duration")

    raw_title = await page.evaluate("document.title")
    title = (raw_title or "").replace(" - YouTube", "")
    min_pct = config.get("min_duration", 70) / 100
    max_pct = config.get("max_duration", 95) / 100
    watch_time = video_len * uniform(min_pct, max_pct)
    duration_str = strftime("%Mm:%Ss", gmtime(watch_time))

    bot_state["workers"][position] = {
        "proxy": proxy_label, "status": "watching",
        "title": title[:60], "duration": duration_str,
    }
    add_log(f"Worker {position} | {proxy_label} | {title[:50]} | {duration_str}")

    # Watch loop with human behavior
    error_streak = 0
    loop_count = int(watch_time / 5)
    human_action_counter = 0
    for _ in range(loop_count):
        if cancel_flag.is_set():
            break
        await page.sleep(_gaussian_delay(5, 0.8))
        try:
            result = await page.evaluate('''
                (() => {
                    try {
                        const p = document.getElementById('movie_player');
                        return {t: p.getCurrentTime(), s: p.getPlayerState()};
                    } catch(e) { return null; }
                })()
            ''')
            if not result:
                error_streak += 1
                if error_streak > 4:
                    raise Exception("Player communication lost")
                continue

            state = result.get("s", -99)
            current_time = result.get("t", 0)

            if state in [-1, 0]:
                break
            if state == 2:  # paused
                await page.evaluate("try { document.getElementById('movie_player').playVideo(); } catch(e) {}")
            if state == 3:  # buffering
                error_streak += 1
                if error_streak > 6:
                    raise Exception("Buffering too long")
            else:
                error_streak = 0
            if current_time >= watch_time:
                break

            # Periodic human-like actions
            human_action_counter += 1
            if human_action_counter % randint(4, 8) == 0:
                await _simulate_human(page)

        except Exception as e:
            if "communication lost" in str(e) or "Buffering" in str(e):
                raise
            error_streak += 1
            if error_streak > 4:
                raise Exception("Player communication lost")

    bot_state["views"] += 1
    title_short = title[:50]
    bot_state["video_stats"][title_short] = bot_state["video_stats"].get(title_short, 0) + 1
    add_log(f"Worker {position} | View #{bot_state['views']} OK", "success")
    return True


async def _worker_multitab_async(position, proxy, proxy_type, config):
    """Async multitab worker using zendriver (CDP) + all enhancements."""
    is_direct = (proxy == "__direct__")
    proxy_pool = config.get("_proxy_pool", [proxy])
    max_retries = 1 if is_direct else 3
    views_per_session = config.get("_views_per_session", 5)
    content_type = config.get("_content_type", "video")
    is_shorts = content_type == "shorts"

    for attempt in range(max_retries):
        if cancel_flag.is_set():
            return

        if attempt > 0 and not is_direct:
            available = [p for p in proxy_pool if not is_bad_proxy(p)]
            if not available:
                break
            proxy = choice(available)

        clean_proxy = proxy if is_direct else strip_proxy_prefix(proxy)
        proxy_label = "Direct" if is_direct else clean_proxy

        if not is_direct and is_bad_proxy(proxy):
            continue

        browser = None
        try:
            bot_state["good_proxies"] += 1
            retry_tag = f" (retry {attempt})" if attempt > 0 else ""
            mode_tag = "shorts" if is_shorts else "video"
            add_log(f"Worker {position} | {proxy_label}{retry_tag} | Starting ({mode_tag} x{views_per_session})")

            headless = config.get("headless", True)
            browser = await start_browser(headless, None if is_direct else clean_proxy, proxy_type, is_shorts)

            bot_state["workers"][position] = {"proxy": proxy_label, "status": "loading"}

            page = await browser.get("about:blank")

            # First tab
            page = await _try_load_video_async(page, browser, config, position, proxy_label)
            if not page:
                if not is_direct:
                    mark_bad_proxy(proxy)
                    bot_state["bad_proxies"] += 1
                raise Exception("Video load failed")

            # Watch based on content type
            if is_shorts:
                await _watch_shorts_async(page, config, position, proxy_label)
            else:
                await _watch_video_async(page, config, position, proxy_label)
            session_views = 1

            # Continue with more tabs
            for tab_i in range(1, views_per_session):
                if cancel_flag.is_set() or bot_state["views"] >= config.get("target_views", 100):
                    break

                bot_state["workers"][position] = {"proxy": proxy_label, "status": f"tab {tab_i+1}/{views_per_session}"}

                try:
                    new_page = await browser.get(config["url"], new_tab=True)
                    await new_page.sleep(_gaussian_delay(4, 1))

                    loaded_page = await _try_load_video_async(new_page, browser, config, position, proxy_label)
                    if not loaded_page:
                        add_log(f"Worker {position} | Tab {tab_i+1} load failed, skipping", "warn")
                        try:
                            await new_page.close()
                        except Exception:
                            pass
                        continue

                    if is_shorts:
                        await _watch_shorts_async(loaded_page, config, position, proxy_label)
                    else:
                        await _watch_video_async(loaded_page, config, position, proxy_label)
                    session_views += 1

                    try:
                        await loaded_page.close()
                    except Exception:
                        pass

                    await asyncio.sleep(_gaussian_delay(2, 0.5))

                except Exception as e:
                    add_log(f"Worker {position} | Tab {tab_i+1} error: {str(e)[:80]}", "warn")

            add_log(f"Worker {position} | Session done: {session_views} views from {proxy_label}", "success")
            return

        except Exception as e:
            err_msg = str(e)[:150]
            if attempt == max_retries - 1:
                bot_state["errors"] += 1
                add_log(f"Worker {position} | FAIL after {attempt+1} tries: {err_msg}", "error")
            else:
                add_log(f"Worker {position} | Retry {attempt+1}: {err_msg}", "warn")
        finally:
            bot_state["workers"].pop(position, None)
            if browser:
                try:
                    browser.stop()
                except Exception:
                    pass


def worker_multitab(position, proxy, proxy_type, config):
    """Thread-safe wrapper: runs async zendriver worker in its own event loop."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_worker_multitab_async(position, proxy, proxy_type, config))
    finally:
        loop.close()


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

    with _bad_proxy_lock:
        _bad_proxy_set.clear()

    # Detect content type
    content_type = _detect_content_type(config["url"])
    config["_content_type"] = content_type

    add_log(f"봇 시작 - 모드: {content_type.upper()} | zendriver + 핑거프린트 스푸핑")

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
        target_valid = min(max(config.get("threads", 5) * 4, config.get("target_views", 100), 30), 200)
        proxy_list = pre_validate_proxies(raw_proxies, proxy_type, max_workers=150, target=target_valid)

    if not proxy_list:
        add_log("유효한 프록시가 없습니다!", "error")
        bot_state["running"] = False
        return

    config["_proxy_pool"] = proxy_list
    config["_max_retries"] = 3

    # Shorts get more views per session (faster per view)
    if content_type == "shorts":
        config["_views_per_session"] = config.get("shorts_loops", 3)
    else:
        config["_views_per_session"] = 5

    target = config.get("target_views", 100)
    max_threads = config.get("threads", 5)
    if use_no_proxy:
        max_threads = 1

    traffic_src = config.get("traffic_source", "mixed")
    bot_state["eta"] = _estimate_eta(target, max_threads, use_no_proxy, len(proxy_list), content_type)
    add_log(f"목표: {target}회 | 스레드: {max_threads} | 프록시: {'없음(직접)' if use_no_proxy else f'{len(proxy_list)}개'}")
    add_log(f"트래픽 소스: {traffic_src} | 핑거프린트: ON | 인간 행동: ON")
    add_log(f"예상 소요 시간: {bot_state['eta']}")

    refetch_count = 0
    position = 0
    batch_num = 0
    while bot_state["views"] < target and not cancel_flag.is_set():
        if not use_no_proxy:
            available = [p for p in proxy_list if not is_bad_proxy(p)]
            if len(available) < max_threads:
                add_log(f"사용 가능한 프록시 부족 ({len(available)}개) - 새 프록시 수집 중...", "warn")
                refetch_count += 1
                if refetch_count > 5:
                    add_log("프록시 재수집 한도 초과, 종료합니다", "error")
                    break
                raw_proxies = fetch_free_proxies(proxy_type)
                if raw_proxies:
                    new_proxies = pre_validate_proxies(raw_proxies, proxy_type, max_workers=150, target=80)
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
        used_set = set()
        for _ in range(batch_size):
            candidates = [p for p in available if p not in used_set]
            if not candidates:
                candidates = available
            pick = choice(candidates)
            used_set.add(pick)
            batch_proxies.append(pick)

        threads = []
        for i, proxy in enumerate(batch_proxies):
            pos = position + i
            t = threading.Thread(target=worker_multitab, args=(pos, proxy, proxy_type, config))
            t.daemon = True
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=600)

        position += batch_size
        batch_num += 1

        if bot_state["views"] >= target:
            break

        if batch_num % 2 == 0 and bot_state["views"] > 0:
            elapsed = time.time() - bot_state["start_time"]
            rate = bot_state["views"] / elapsed
            remaining = target - bot_state["views"]
            if rate > 0:
                eta_sec = int(remaining / rate)
                bot_state["eta"] = _format_duration(eta_sec)
                if batch_num % 4 == 0:
                    add_log(f"진행: {bot_state['views']}/{target} | 속도: {rate*60:.1f}회/분 | 남은 시간: {bot_state['eta']}")

        sleep(1)

    elapsed = int(time.time() - bot_state["start_time"])
    add_log(f"봇 종료 - 총 조회수: {bot_state['views']} | 소요 시간: {_format_duration(elapsed)}", "success")
    bot_state["running"] = False
    bot_state["eta"] = ""


def _format_duration(seconds):
    if seconds < 60:
        return f"{seconds}초"
    elif seconds < 3600:
        return f"{seconds // 60}분 {seconds % 60}초"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}시간 {m}분"


def _estimate_eta(target_views, threads, is_direct, proxy_count, content_type="video"):
    if content_type == "shorts":
        # Shorts: ~10s per view (no min watch time)
        per_view = 10
        if is_direct:
            total_sec = int(target_views * per_view / 0.95)
        else:
            effective = min(threads, proxy_count)
            total_sec = int(target_views * per_view / effective / 0.6)
    else:
        if is_direct:
            total_sec = int(target_views * 90 / 0.95)
        else:
            effective_threads = min(threads, proxy_count)
            views_per_session = 5
            session_time = 30 + (views_per_session * 60)
            views_per_session_effective = views_per_session * 0.6
            sessions_needed = target_views / views_per_session_effective
            total_sec = int(sessions_needed * session_time / effective_threads)

    return _format_duration(total_sec)


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
        "traffic_source": data.get("traffic_source", "mixed"),
        "shorts_loops": int(data.get("shorts_loops", 3)),
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
        "eta": bot_state.get("eta", ""),
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
    data = request.json or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"valid": False, "error": "URL을 입력하세요"})

    vid_match = re.search(r"(?:v=|youtu\.be/|shorts/)([0-9A-Za-z_-]{11})", url)
    if not vid_match:
        return jsonify({"valid": False, "error": "유효한 YouTube URL이 아닙니다"})

    video_id = vid_match.group(1)
    is_shorts = "/shorts/" in url

    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = requests.get(oembed_url, timeout=10)
        if resp.status_code != 200:
            return jsonify({"valid": False, "error": "영상을 찾을 수 없습니다"})

        oembed = resp.json()
        title = oembed.get("title", "Unknown")
        author = oembed.get("author_name", "Unknown")
        thumbnail = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

        page_resp = requests.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": CHROME_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=10,
        )
        view_count = ""
        duration_text = ""
        if page_resp.status_code == 200:
            vc_match = re.search(r'"viewCount"\s*:\s*"(\d+)"', page_resp.text)
            if vc_match:
                view_count = f"{int(vc_match.group(1)):,}"
            dur_match = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', page_resp.text)
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
            "is_shorts": is_shorts,
        })

    except Exception as e:
        return jsonify({"valid": False, "error": f"검증 실패: {str(e)}"})


@app.route("/api/verify-result", methods=["POST"])
def api_verify_result():
    data = request.json or {}
    url = data.get("url", "").strip()

    vid_match = re.search(r"(?:v=|youtu\.be/|shorts/)([0-9A-Za-z_-]{11})", url)
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
            vc_match = re.search(r'"viewCount"\s*:\s*"(\d+)"', page_resp.text)
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
    print("  YouTube View Web Dashboard v2")
    print("  zendriver + fingerprint + traffic diversity")
    print("  http://127.0.0.1:5000")
    print("=" * 50 + "\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
