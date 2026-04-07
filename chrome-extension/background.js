/**
 * YT View Enhancer — Background Service Worker
 * Opens YouTube videos in background tabs and manages view sessions.
 */

const DEFAULT_CONFIG = {
  url: "",
  targetViews: 10,
  minWatchPct: 70,
  maxWatchPct: 95,
  delayBetween: 5,  // seconds between tabs
  maxConcurrent: 1,
  running: false,
};

let state = {
  running: false,
  views: 0,
  errors: 0,
  targetViews: 0,
  activeTabs: new Set(),
  logs: [],
};

function addLog(msg, level = "info") {
  const ts = new Date().toLocaleTimeString();
  state.logs.push({ ts, msg, level });
  if (state.logs.length > 200) state.logs.shift();
  // Broadcast to popup if open
  chrome.runtime.sendMessage({ type: "log", data: { ts, msg, level } }).catch(() => {});
}

function saveState() {
  chrome.storage.local.set({
    state: {
      running: state.running,
      views: state.views,
      errors: state.errors,
      targetViews: state.targetViews,
      logs: state.logs.slice(-100),
    },
  });
}

// Listen for messages from popup and content scripts
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "start") {
    startBot(msg.config);
    sendResponse({ ok: true });
  } else if (msg.type === "stop") {
    stopBot();
    sendResponse({ ok: true });
  } else if (msg.type === "getState") {
    sendResponse({
      running: state.running,
      views: state.views,
      errors: state.errors,
      targetViews: state.targetViews,
      logs: state.logs.slice(-100),
    });
  } else if (msg.type === "viewComplete") {
    handleViewComplete(sender.tab?.id);
  } else if (msg.type === "viewError") {
    handleViewError(sender.tab?.id, msg.error);
  }
  return true;
});

async function startBot(config) {
  if (state.running) return;

  state.running = true;
  state.views = 0;
  state.errors = 0;
  state.targetViews = config.targetViews || 10;
  state.activeTabs = new Set();
  state.logs = [];

  // Store config for content scripts
  await chrome.storage.local.set({ config });

  addLog(`봇 시작 - 목표: ${state.targetViews}회`);
  saveState();

  scheduleNextView(config);
}

function stopBot() {
  state.running = false;
  addLog("봇 중지됨", "warn");

  // Close all active tabs
  for (const tabId of state.activeTabs) {
    chrome.tabs.remove(tabId).catch(() => {});
  }
  state.activeTabs.clear();
  saveState();
}

async function scheduleNextView(config) {
  if (!state.running) return;
  if (state.views >= state.targetViews) {
    addLog(`목표 달성! 총 조회수: ${state.views}`, "success");
    state.running = false;
    saveState();
    return;
  }

  // Check concurrent limit
  if (state.activeTabs.size >= (config.maxConcurrent || 1)) {
    // Wait and retry
    setTimeout(() => scheduleNextView(config), 2000);
    return;
  }

  try {
    const url = config.url;
    if (!url) {
      addLog("URL이 설정되지 않았습니다", "error");
      stopBot();
      return;
    }

    // Open video in a new background tab
    const tab = await chrome.tabs.create({ url, active: false });
    state.activeTabs.add(tab.id);
    addLog(`탭 #${tab.id} 열림 - 영상 로딩 중...`);
    saveState();

    // Schedule next if we still need more
    const delay = (config.delayBetween || 5) * 1000;
    setTimeout(() => scheduleNextView(config), delay);
  } catch (e) {
    state.errors++;
    addLog(`탭 생성 실패: ${e.message}`, "error");
    saveState();
    setTimeout(() => scheduleNextView(config), 3000);
  }
}

async function handleViewComplete(tabId) {
  if (tabId && state.activeTabs.has(tabId)) {
    state.activeTabs.delete(tabId);
    state.views++;
    addLog(`View #${state.views} 완료 (탭 #${tabId})`, "success");

    // Close the tab
    chrome.tabs.remove(tabId).catch(() => {});
    saveState();

    // Check if target reached
    if (state.views >= state.targetViews) {
      addLog(`목표 달성! 총 조회수: ${state.views}`, "success");
      state.running = false;
      saveState();
    } else {
      // Re-trigger scheduling
      const stored = await chrome.storage.local.get("config");
      if (stored.config) scheduleNextView(stored.config);
    }
  }
}

async function handleViewError(tabId, error) {
  if (tabId && state.activeTabs.has(tabId)) {
    state.activeTabs.delete(tabId);
    state.errors++;
    addLog(`탭 #${tabId} 오류: ${error}`, "error");

    chrome.tabs.remove(tabId).catch(() => {});
    saveState();

    // Continue with next view
    const stored = await chrome.storage.local.get("config");
    if (stored.config && state.running) scheduleNextView(stored.config);
  }
}

// Clean up on tab close (user manually closed a tab)
chrome.tabs.onRemoved.addListener((tabId) => {
  if (state.activeTabs.has(tabId)) {
    state.activeTabs.delete(tabId);
    saveState();
  }
});
