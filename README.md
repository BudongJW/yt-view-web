# YT View Web

Web dashboard-based YouTube view bot with browser automation, fingerprint spoofing, and proxy rotation.

## Features

- **Web Dashboard** — Real-time control panel with live stats, logs, and progress tracking
- **Chrome Extension** — Lightweight browser extension for quick view boosting
- **zendriver (CDP)** — Headless Chrome automation via Chrome DevTools Protocol
- **Fingerprint Spoofing** — Canvas, WebGL, AudioContext, WebRTC, navigator property randomization
- **Traffic Source Diversity** — Mixed traffic from direct, external referrer (Google/Bing/Twitter), and YouTube search
- **Smart Proxy System** — Auto-fetch from 25+ free proxy sources + ProxyBroker2, 3-stage warmup validation (httpbin → Google → YouTube), health scoring with blacklist/rate-limit
- **Shorts Support** — Automatic detection and optimized playback loop for YouTube Shorts
- **Human Behavior Simulation** — Random scrolling, Gaussian-distributed delays, variable watch duration
- **Docker Ready** — One-command VPS deployment with Docker Compose

## Requirements

- Python 3.10+
- Google Chrome or Chromium installed
- (Optional) Docker & Docker Compose for containerized deployment

## Installation

### Option 1: Local (Windows / macOS / Linux)

```bash
git clone https://github.com/BudongJW/yt-view-web.git
cd yt-view-web
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000` in your browser.

### Option 2: Docker

```bash
git clone https://github.com/BudongJW/yt-view-web.git
cd yt-view-web
docker compose up -d --build
```

Dashboard available at `http://<your-ip>:5000`.

### Option 3: VPS One-Click Deploy (Ubuntu 22.04+)

```bash
curl -fsSL https://raw.githubusercontent.com/BudongJW/yt-view-web/master/deploy.sh | bash
```

This automatically installs Docker, clones the repo, and starts the service.

### Chrome Extension (Optional)

1. Open `chrome://extensions/` in Chrome
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select the `chrome-extension/` folder
4. Click the extension icon to open the popup dashboard

## Usage

### Web Dashboard

1. Open `http://localhost:5000`
2. Paste a YouTube video or Shorts URL
3. Configure settings:
   - **Target Views** — Number of views to generate
   - **Threads** — Concurrent browser instances (recommended: 3–5 with proxies, 1–2 without)
   - **Proxy Type** — HTTP / SOCKS4 / SOCKS5 / None
   - **Proxy Source** — Auto (free lists) / Custom / None
   - **Traffic Source** — Mixed / Direct / External / Search
   - **Watch Duration** — Min/Max percentage of video to watch (default: 70–95%)
   - **Playback Speed** — 1x / 1.5x / 2x
   - **Shorts Loops** — Number of replay loops per Shorts session
   - **Bandwidth Saving** — Forces lowest quality (144p)
4. Click **Start** and monitor progress in real-time
5. Use **Verify Result** to check the actual view count on YouTube

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/start` | Start the bot with config JSON |
| `POST` | `/api/stop` | Stop the bot |
| `GET` | `/api/status` | Get current status, stats, and worker info |
| `GET` | `/api/logs` | Get detailed logs (filterable by level) |
| `POST` | `/api/validate-url` | Validate a YouTube URL and fetch video info |
| `POST` | `/api/verify-result` | Check actual view count after bot run |
| `POST` | `/api/fetch-proxies` | Manually trigger proxy collection |

### Example API Call

```bash
curl -X POST http://localhost:5000/api/start \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=VIDEO_ID",
    "target_views": 50,
    "threads": 3,
    "proxy_type": "http",
    "proxy_source": "auto",
    "traffic_source": "mixed",
    "headless": true
  }'
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `5000` | Server port |
| `CHROME_PATH` | (auto-detect) | Path to Chrome/Chromium binary |

## Docker Resource Tuning

Edit `docker-compose.yml` to adjust:

```yaml
deploy:
  resources:
    limits:
      cpus: "3"      # CPU cores
      memory: 8G     # RAM limit
shm_size: 2g         # Shared memory for Chrome
```

## Disclaimer

This tool is intended for educational and research purposes only. Use responsibly and in compliance with YouTube's Terms of Service.

---

# YT View Web (中文)

基于 Web 仪表盘的 YouTube 观看量工具，集成浏览器自动化、指纹伪装和代理轮换。

## 功能特性

- **Web 仪表盘** — 实时控制面板，包含实时统计、日志和进度追踪
- **Chrome 扩展** — 轻量级浏览器扩展，快速提升观看量
- **zendriver (CDP)** — 通过 Chrome DevTools Protocol 实现无头浏览器自动化
- **指纹伪装** — Canvas、WebGL、AudioContext、WebRTC、navigator 属性随机化
- **流量来源多样化** — 混合直接访问、外部来源（Google/Bing/Twitter）和 YouTube 搜索流量
- **智能代理系统** — 自动从 25+ 免费代理源 + ProxyBroker2 获取，3 阶段预热验证（httpbin → Google → YouTube），健康评分 + 黑名单/限速
- **Shorts 支持** — 自动检测并优化 YouTube Shorts 循环播放
- **人类行为模拟** — 随机滚动、高斯分布延迟、可变观看时长
- **Docker 部署** — 一键 VPS 部署，支持 Docker Compose

## 环境要求

- Python 3.10+
- 已安装 Google Chrome 或 Chromium
- （可选）Docker 和 Docker Compose 用于容器化部署

## 安装方法

### 方式一：本地安装（Windows / macOS / Linux）

```bash
git clone https://github.com/BudongJW/yt-view-web.git
cd yt-view-web
pip install -r requirements.txt
python app.py
```

在浏览器中打开 `http://localhost:5000`。

### 方式二：Docker 部署

```bash
git clone https://github.com/BudongJW/yt-view-web.git
cd yt-view-web
docker compose up -d --build
```

仪表盘地址：`http://<你的IP>:5000`。

### 方式三：VPS 一键部署（Ubuntu 22.04+）

```bash
curl -fsSL https://raw.githubusercontent.com/BudongJW/yt-view-web/master/deploy.sh | bash
```

自动安装 Docker、克隆仓库并启动服务。

### Chrome 扩展（可选）

1. 在 Chrome 中打开 `chrome://extensions/`
2. 启用右上角的 **开发者模式**
3. 点击 **加载已解压的扩展程序** → 选择 `chrome-extension/` 文件夹
4. 点击扩展图标打开弹出式仪表盘

## 使用方法

### Web 仪表盘

1. 打开 `http://localhost:5000`
2. 粘贴 YouTube 视频或 Shorts 链接
3. 配置参数：
   - **目标观看数** — 要生成的观看次数
   - **线程数** — 并发浏览器实例数（推荐：使用代理时 3–5，不使用代理时 1–2）
   - **代理类型** — HTTP / SOCKS4 / SOCKS5 / 无
   - **代理来源** — 自动（免费列表）/ 自定义 / 无
   - **流量来源** — 混合 / 直接 / 外部 / 搜索
   - **观看时长** — 视频观看百分比（默认：70–95%）
   - **播放速度** — 1x / 1.5x / 2x
   - **Shorts 循环** — 每次 Shorts 会话的重播次数
   - **节省带宽** — 强制使用最低画质（144p）
4. 点击 **开始** 并实时监控进度
5. 使用 **验证结果** 查看 YouTube 上的实际观看数

### API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/start` | 使用配置 JSON 启动机器人 |
| `POST` | `/api/stop` | 停止机器人 |
| `GET` | `/api/status` | 获取当前状态、统计和工作线程信息 |
| `GET` | `/api/logs` | 获取详细日志（可按级别筛选） |
| `POST` | `/api/validate-url` | 验证 YouTube 链接并获取视频信息 |
| `POST` | `/api/verify-result` | 运行后检查实际观看数 |
| `POST` | `/api/fetch-proxies` | 手动触发代理收集 |

### API 调用示例

```bash
curl -X POST http://localhost:5000/api/start \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=VIDEO_ID",
    "target_views": 50,
    "threads": 3,
    "proxy_type": "http",
    "proxy_source": "auto",
    "traffic_source": "mixed",
    "headless": true
  }'
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HOST` | `0.0.0.0` | 服务器绑定地址 |
| `PORT` | `5000` | 服务器端口 |
| `CHROME_PATH` | （自动检测） | Chrome/Chromium 可执行文件路径 |

## Docker 资源调优

编辑 `docker-compose.yml` 进行调整：

```yaml
deploy:
  resources:
    limits:
      cpus: "3"      # CPU 核心数
      memory: 8G     # 内存限制
shm_size: 2g         # Chrome 共享内存
```

## 免责声明

本工具仅用于教育和研究目的。请负责任地使用，并遵守 YouTube 的服务条款。
