"""
YouTube View Web — 웹 대시보드 기반 YouTube 뷰어 봇
nodriver (CDP) 기반 — chromedriver 불필요, RAM 대폭 절감
"""
import asyncio
import io
import json
import os
import sys
import threading
import time
from datetime import datetime
from random import choice, randint, uniform
from time import gmtime, sleep, strftime

import nodriver as uc
import requests
from flask import Flask, jsonify, render_template, request

# Fix Windows cp949 encoding crash (emoji/unicode in nodriver output)
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


# ── nodriver (CDP) Browser ──
VIEWPORTS = [
    (2560, 1440), (1920, 1080), (1440, 900),
    (1536, 864), (1366, 768), (1280, 1024), (1024, 768),
]

CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.178 Safari/537.36"


async def start_browser(headless=True, proxy=None, proxy_type="http"):
    """Start a nodriver browser with optimized flags. No chromedriver needed."""
    vp = choice(VIEWPORTS)
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
        f"--user-agent={CHROME_UA}",
    ]
    if proxy:
        args.append(f"--proxy-server={proxy_type}://{proxy}")

    browser = await uc.start(headless=headless, browser_args=args)
    return browser


async def _try_load_video_async(page, browser, config, position, proxy_label):
    """Navigate to YouTube video and wait for player. Returns True on success."""
    url = config["url"]

    page = await browser.get(url)
    await page.sleep(4)

    # Handle supported_browsers redirect
    cur_url = page.url or ""
    if "supported_browsers" in cur_url:
        add_log(f"Worker {position} | supported_browsers redirect - retrying", "warn")
        page = await browser.get(url)
        await page.sleep(4)
        cur_url = page.url or ""

    # Handle consent redirect
    if "consent" in cur_url:
        # Try clicking accept button via JS
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

    # Wait for player (poll up to 40s)
    for _ in range(20):
        has_player = await page.evaluate('!!document.getElementById("movie_player")')
        if has_player:
            await page.sleep(3)
            return page
        await page.sleep(2)

    cur_url = page.url or ""
    if "supported_browsers" in cur_url:
        add_log(f"Worker {position} | {proxy_label} | YouTube blocked (unsupported browser)", "warn")
    else:
        add_log(f"Worker {position} | {proxy_label} | Player load failed | url={cur_url[:80]}", "warn")
    return None


async def _watch_video_async(page, config, position, proxy_label):
    """Play and watch the video via CDP. Returns True if view was counted."""
    # Set lowest quality via JS
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

    # Change playback speed
    speed = config.get("playback_speed", 1)
    if speed != 1:
        await page.evaluate(f"try {{ document.querySelector('video').playbackRate = {speed}; }} catch(e) {{}}")

    # Get video duration (retry up to 20 times)
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
        "proxy": proxy_label,
        "status": "watching",
        "title": title[:60],
        "duration": duration_str,
    }

    add_log(f"Worker {position} | {proxy_label} | {title[:50]} | {duration_str}")

    # Watch loop
    error_streak = 0
    loop_count = int(watch_time / 5)
    for _ in range(loop_count):
        if cancel_flag.is_set():
            break
        await page.sleep(5)
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

            if state in [-1, 0]:  # unstarted or ended
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
        except Exception as e:
            if "communication lost" in str(e) or "Buffering" in str(e):
                raise
            error_streak += 1
            if error_streak > 4:
                raise Exception("Player communication lost")

    # Count view
    bot_state["views"] += 1
    title_short = title[:50]
    bot_state["video_stats"][title_short] = bot_state["video_stats"].get(title_short, 0) + 1
    add_log(f"Worker {position} | View #{bot_state['views']} OK", "success")
    return True


async def _worker_multitab_async(position, proxy, proxy_type, config):
    """Async multitab worker using nodriver (CDP). One Chrome, multiple views."""
    is_direct = (proxy == "__direct__")
    proxy_pool = config.get("_proxy_pool", [proxy])
    max_retries = 1 if is_direct else 3
    views_per_session = config.get("_views_per_session", 5)

    for attempt in range(max_retries):
        if cancel_flag.is_set():
            return

        # Rotate proxy on retry
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
            add_log(f"Worker {position} | {proxy_label}{retry_tag} | Starting (nodriver x{views_per_session})")

            headless = config.get("headless", True)
            browser = await start_browser(headless, None if is_direct else clean_proxy, proxy_type)

            bot_state["workers"][position] = {"proxy": proxy_label, "status": "loading"}

            # Get initial page
            page = await browser.get("about:blank")

            # First tab: if this fails, retry with different proxy
            page = await _try_load_video_async(page, browser, config, position, proxy_label)
            if not page:
                if not is_direct:
                    mark_bad_proxy(proxy)
                    bot_state["bad_proxies"] += 1
                raise Exception("Video load failed")

            # First tab succeeded - watch it
            await _watch_video_async(page, config, position, proxy_label)
            session_views = 1

            # Continue with more tabs in the same Chrome
            for tab_i in range(1, views_per_session):
                if cancel_flag.is_set() or bot_state["views"] >= config.get("target_views", 100):
                    break

                bot_state["workers"][position] = {"proxy": proxy_label, "status": f"tab {tab_i+1}/{views_per_session}"}

                try:
                    # Open new tab via browser.get with new_tab=True
                    new_page = await browser.get(config["url"], new_tab=True)
                    await new_page.sleep(4)

                    # Check player on new tab
                    loaded_page = await _try_load_video_async(new_page, browser, config, position, proxy_label)
                    if not loaded_page:
                        add_log(f"Worker {position} | Tab {tab_i+1} load failed, skipping", "warn")
                        try:
                            await new_page.close()
                        except Exception:
                            pass
                        continue

                    await _watch_video_async(loaded_page, config, position, proxy_label)
                    session_views += 1

                    # Close tab
                    try:
                        await loaded_page.close()
                    except Exception:
                        pass

                    await asyncio.sleep(uniform(1, 3))

                except Exception as e:
                    add_log(f"Worker {position} | Tab {tab_i+1} error: {str(e)[:80]}", "warn")

            add_log(f"Worker {position} | Session done: {session_views} views from {proxy_label}", "success")
            return  # Success

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
    """Thread-safe wrapper: runs async nodriver worker in its own event loop."""
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
        # Scale validated proxy target: at least 2x threads, cap 200
        target_valid = min(max(config.get("threads", 5) * 4, config.get("target_views", 100), 30), 200)
        proxy_list = pre_validate_proxies(raw_proxies, proxy_type, max_workers=150, target=target_valid)

    if not proxy_list:
        add_log("유효한 프록시가 없습니다!", "error")
        bot_state["running"] = False
        return

    # Pass full proxy pool and retry config to workers
    config["_proxy_pool"] = proxy_list
    config["_max_retries"] = 3
    config["_views_per_session"] = 5  # views per Chrome instance (multitab)

    target = config.get("target_views", 100)
    max_threads = config.get("threads", 5)
    if use_no_proxy:
        max_threads = 1

    # Estimate completion time
    bot_state["eta"] = _estimate_eta(target, max_threads, use_no_proxy, len(proxy_list))
    add_log(f"목표: {target}회 | 스레드: {max_threads} | 프록시: {'없음(직접)' if use_no_proxy else f'{len(proxy_list)}개'}")
    add_log(f"멀티탭 모드: Chrome당 {config['_views_per_session']}회 시청 (RAM 절약)")
    add_log(f"예상 소요 시간: {bot_state['eta']}")

    refetch_count = 0
    position = 0
    batch_num = 0
    while bot_state["views"] < target and not cancel_flag.is_set():
        # Filter out known-bad proxies for batch selection
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
            # Try to pick unique proxies for each worker in batch
            candidates = [p for p in available if p not in used_set]
            if not candidates:
                candidates = available
            pick = choice(candidates)
            used_set.add(pick)
            batch_proxies.append(pick)

        # Use multitab workers for efficiency
        threads = []
        for i, proxy in enumerate(batch_proxies):
            pos = position + i
            t = threading.Thread(target=worker_multitab, args=(pos, proxy, proxy_type, config))
            t.daemon = True
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=600)  # longer timeout for multitab sessions

        position += batch_size
        batch_num += 1

        if bot_state["views"] >= target:
            break

        # Update ETA based on actual throughput every 2 batches
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
    """Format seconds into human-readable duration string."""
    if seconds < 60:
        return f"{seconds}초"
    elif seconds < 3600:
        return f"{seconds // 60}분 {seconds % 60}초"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}시간 {m}분"


def _estimate_eta(target_views, threads, is_direct, proxy_count):
    """Estimate completion time based on configuration.

    Multitab mode: each Chrome does ~5 views before cycling.
    - ~60s per view within a session (no driver restart overhead)
    - ~30s overhead per Chrome startup
    - ~60% success rate per tab after first
    """
    if is_direct:
        total_sec = int(target_views * 90 / 0.95)
    else:
        effective_threads = min(threads, proxy_count)
        views_per_session = 5
        session_time = 30 + (views_per_session * 60)  # startup + watch time
        views_per_session_effective = views_per_session * 0.6  # success rate
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
