/**
 * YT View Enhancer — Content Script
 * Injected into YouTube watch pages. Plays the video and watches for the configured duration.
 */

(async function () {
  "use strict";

  // Get config from storage
  const stored = await chrome.storage.local.get("config");
  const config = stored.config;
  if (!config) return;

  const minPct = (config.minWatchPct || 70) / 100;
  const maxPct = (config.maxWatchPct || 95) / 100;

  // Wait for video player to be ready
  function waitForPlayer(timeout = 30000) {
    return new Promise((resolve, reject) => {
      const start = Date.now();
      const check = () => {
        const player = document.getElementById("movie_player");
        if (player && typeof player.getDuration === "function" && player.getDuration() > 0) {
          resolve(player);
          return;
        }
        if (Date.now() - start > timeout) {
          reject(new Error("Player load timeout"));
          return;
        }
        setTimeout(check, 500);
      };
      check();
    });
  }

  // Dismiss consent dialogs
  function dismissDialogs() {
    // Cookie consent
    const consentBtn = document.querySelector(
      'button[aria-label*="Accept"], tp-yt-paper-button[aria-label*="Accept"], ' +
      'button.yt-spec-button-shape-next[aria-label*="Accept"], ' +
      '[aria-label="Agree to the use of cookies"]'
    );
    if (consentBtn) consentBtn.click();

    // "Are you still watching?" / age gate
    const dismissBtns = document.querySelectorAll(
      "#dismiss-button button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern"
    );
    dismissBtns.forEach((btn) => btn.click());
  }

  // Skip ads
  function skipAds() {
    const skipBtn = document.querySelector(
      ".ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-skip-ad-button"
    );
    if (skipBtn) {
      skipBtn.click();
      return true;
    }
    return false;
  }

  try {
    // Small delay to let page settle
    await new Promise((r) => setTimeout(r, 2000));
    dismissDialogs();

    const player = await waitForPlayer();
    const duration = player.getDuration();
    const watchTarget = duration * (minPct + Math.random() * (maxPct - minPct));

    // Mute to avoid noise from background tabs
    const video = document.querySelector("video");
    if (video) {
      video.muted = true;
      video.volume = 0;
    }

    // Start playback
    player.playVideo();

    // Skip pre-roll ads
    let adCheckCount = 0;
    while (adCheckCount < 60) {
      await new Promise((r) => setTimeout(r, 1000));
      dismissDialogs();
      skipAds();

      const adPlaying = document.querySelector(".ad-showing, .ytp-ad-player-overlay");
      if (!adPlaying) break;
      adCheckCount++;
    }

    // Ensure playing
    if (player.getPlayerState() !== 1) {
      player.playVideo();
    }

    // Watch loop
    let errorStreak = 0;
    const checkInterval = 3000;
    const maxChecks = Math.ceil((watchTarget / (checkInterval / 1000)) * 1.5);

    for (let i = 0; i < maxChecks; i++) {
      await new Promise((r) => setTimeout(r, checkInterval));

      try {
        dismissDialogs();
        skipAds();

        const currentTime = player.getCurrentTime();
        const playerState = player.getPlayerState();

        // -1=unstarted, 0=ended, 1=playing, 2=paused, 3=buffering
        if (playerState === 0 || currentTime >= watchTarget) {
          break; // Done
        }
        if (playerState === 2) {
          player.playVideo();
        }
        if (playerState === 3) {
          errorStreak++;
          if (errorStreak > 10) {
            throw new Error("Buffering too long");
          }
        } else {
          errorStreak = 0;
        }
      } catch (e) {
        errorStreak++;
        if (errorStreak > 5) {
          throw new Error("Player communication lost");
        }
      }
    }

    // Notify background that view is complete
    chrome.runtime.sendMessage({ type: "viewComplete" });
  } catch (e) {
    chrome.runtime.sendMessage({ type: "viewError", error: e.message });
  }
})();
