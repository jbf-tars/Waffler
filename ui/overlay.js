/**
 * VoiceFlow Overlay — JS animation + level bridge
 * Works as:
 *   (a) standalone HTML loaded by the pywebview overlay window
 *   (b) pywebview exposes window.pywebview.api.cancel() / .stop()
 */

"use strict";

// ── State ─────────────────────────────────────────────────────────────
const NUM_BARS = 10;
let barEls = [];
let currentLevels  = new Array(NUM_BARS).fill(0);
let targetLevels   = new Array(NUM_BARS).fill(0);
let animFrameId    = null;
let idleMode       = true;

// ── Init ──────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  barEls = Array.from(document.querySelectorAll(".bar"));
  startIdleAnimation();
  requestAnimationFrame(animLoop);
});

// ── Idle shimmer ──────────────────────────────────────────────────────
function startIdleAnimation() {
  idleMode = true;
  barEls.forEach(b => b.classList.add("idle-anim"));
}

function stopIdleAnimation() {
  idleMode = false;
  barEls.forEach(b => b.classList.remove("idle-anim"));
}

// ── Animation loop ────────────────────────────────────────────────────
function animLoop() {
  if (!idleMode) {
    let changed = false;
    for (let i = 0; i < NUM_BARS; i++) {
      const diff = targetLevels[i] - currentLevels[i];
      if (Math.abs(diff) > 0.005) {
        currentLevels[i] += diff * 0.38;
        changed = true;
      } else {
        currentLevels[i] = targetLevels[i];
      }
    }
    if (changed) renderBars();
  }
  animFrameId = requestAnimationFrame(animLoop);
}

function renderBars() {
  const MIN_H = 4;
  const MAX_H = 32;
  barEls.forEach((el, i) => {
    const lvl = currentLevels[i];
    const h   = MIN_H + lvl * (MAX_H - MIN_H);
    el.style.height = h + "px";

    // colour class
    el.classList.remove("low", "mid", "high", "peak");
    if      (lvl > 0.80) el.classList.add("peak");
    else if (lvl > 0.55) el.classList.add("high");
    else if (lvl > 0.30) el.classList.add("mid");
    else                  el.classList.add("low");
  });
}

// ── Public API (called from Python via pywebview evaluate_js) ─────────

/**
 * updateLevel(level)
 * level: 0.0 – 1.0 RMS normalised
 * Called every ~50ms from the Python recording thread.
 */
window.updateLevel = function(level) {
  if (idleMode) stopIdleAnimation();
  level = Math.max(0, Math.min(1, level));
  const t = Date.now() / 1000;
  for (let i = 0; i < NUM_BARS; i++) {
    const wave  = Math.sin(i * 0.9 + t * 4.5) * 0.25 + 0.75;
    const noise = 0.85 + Math.random() * 0.15;
    targetLevels[i] = level * wave * noise;
  }
};

/**
 * resetBars()  — call when recording stops
 */
window.resetBars = function() {
  targetLevels   = new Array(NUM_BARS).fill(0);
  currentLevels  = new Array(NUM_BARS).fill(0);
  renderBars();
  startIdleAnimation();
};

// ── Button handlers ───────────────────────────────────────────────────
function handleCancel() {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.cancel_recording();
  } else {
    // Standalone subprocess: signal via JS console (caught by pywebview)
    console.log(JSON.stringify({ event: "cancel" }));
  }
}

function handleStop() {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.stop_recording();
  } else {
    console.log(JSON.stringify({ event: "stop" }));
  }
}
