/**
 * YT View Enhancer — Popup Script
 */

const $ = (sel) => document.querySelector(sel);

// Elements
const urlInput = $("#urlInput");
const btnStart = $("#btnStart");
const btnStop = $("#btnStop");
const statusBadge = $("#statusBadge");
const viewCount = $("#viewCount");
const errorCount = $("#errorCount");
const targetCount = $("#targetCount");
const progressFill = $("#progressFill");
const logArea = $("#logArea");
const preview = $("#preview");

// URL preview with debounce
let urlTimer = null;
urlInput.addEventListener("input", () => {
  clearTimeout(urlTimer);
  urlTimer = setTimeout(validateUrl, 500);
});

async function validateUrl() {
  const url = urlInput.value.trim();
  const match = url.match(/(?:v=|youtu\.be\/)([\w-]{11})/);
  if (!match) {
    preview.style.display = "none";
    return;
  }
  const videoId = match[1];
  try {
    const resp = await fetch(`https://noembed.com/embed?url=https://www.youtube.com/watch?v=${videoId}`);
    const data = await resp.json();
    if (data.title) {
      $("#previewImg").src = `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`;
      $("#previewTitle").textContent = data.title;
      $("#previewChannel").textContent = data.author_name || "";
      preview.style.display = "block";
    }
  } catch {
    preview.style.display = "none";
  }
}

// Start bot
btnStart.addEventListener("click", () => {
  const url = urlInput.value.trim();
  if (!url) {
    alert("YouTube URL을 입력하세요");
    return;
  }

  const config = {
    url,
    targetViews: parseInt($("#targetViews").value) || 10,
    maxConcurrent: parseInt($("#maxConcurrent").value) || 1,
    minWatchPct: parseInt($("#minWatchPct").value) || 70,
    maxWatchPct: parseInt($("#maxWatchPct").value) || 95,
    delayBetween: parseInt($("#delayBetween").value) || 5,
  };

  chrome.runtime.sendMessage({ type: "start", config }, () => {
    setRunning(true);
  });
});

// Stop bot
btnStop.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "stop" }, () => {
    setRunning(false);
  });
});

function setRunning(running) {
  if (running) {
    btnStart.style.display = "none";
    btnStop.style.display = "block";
    statusBadge.textContent = "RUNNING";
    statusBadge.classList.add("running");
    // Disable inputs
    urlInput.disabled = true;
    document.querySelectorAll('input[type="number"]').forEach((i) => (i.disabled = true));
  } else {
    btnStart.style.display = "block";
    btnStop.style.display = "none";
    statusBadge.textContent = "STOPPED";
    statusBadge.classList.remove("running");
    urlInput.disabled = false;
    document.querySelectorAll('input[type="number"]').forEach((i) => (i.disabled = false));
  }
}

function addLogLine(ts, msg, level) {
  const line = document.createElement("div");
  line.className = `log-line ${level}`;
  line.textContent = `[${ts}] ${msg}`;
  logArea.appendChild(line);
  logArea.scrollTop = logArea.scrollHeight;
}

// Listen for log messages from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "log") {
    addLogLine(msg.data.ts, msg.data.msg, msg.data.level);
  }
});

// Poll state every 2s
function refreshState() {
  chrome.runtime.sendMessage({ type: "getState" }, (resp) => {
    if (!resp) return;
    setRunning(resp.running);
    viewCount.textContent = resp.views || 0;
    errorCount.textContent = resp.errors || 0;
    targetCount.textContent = resp.targetViews || 0;

    const pct = resp.targetViews > 0 ? Math.min(100, (resp.views / resp.targetViews) * 100) : 0;
    progressFill.style.width = `${pct}%`;
  });
}

// Init: load saved state and config
async function init() {
  // Restore config from storage
  const stored = await chrome.storage.local.get(["config", "state"]);
  if (stored.config) {
    urlInput.value = stored.config.url || "";
    $("#targetViews").value = stored.config.targetViews || 10;
    $("#maxConcurrent").value = stored.config.maxConcurrent || 1;
    $("#minWatchPct").value = stored.config.minWatchPct || 70;
    $("#maxWatchPct").value = stored.config.maxWatchPct || 95;
    $("#delayBetween").value = stored.config.delayBetween || 5;
    validateUrl();
  }
  if (stored.state) {
    // Replay logs
    (stored.state.logs || []).forEach((l) => addLogLine(l.ts, l.msg, l.level));
  }
  refreshState();
  setInterval(refreshState, 2000);
}

init();
