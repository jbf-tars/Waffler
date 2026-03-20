# Hotkey Customization & App Icon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users rebind the recording hotkey via a game-style key capture UI, and replace the default Python icon with the Waffler brand icon.

**Architecture:** The hotkey listener (`windows_hotkey.py`) becomes configurable via a `keys` parameter. A new Settings UI section + modal popup lets users pick a key combo. The backend persists to `settings.json` and hot-restarts the listener. The app window gets the Waffler `.ico` passed to pywebview.

**Tech Stack:** Python (pywebview, ctypes/Win32), HTML/CSS/JS (frontend), settings.json (persistence)

**Spec:** `docs/superpowers/specs/2026-03-20-hotkey-customization-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/windows_hotkey.py` | Modify | Accept configurable `keys` param, generic key tracking, configurable polling fallback |
| `app.py` | Modify | New API methods (`get_hotkey_config`, `save_hotkey_config`), store listener reference, update `test_hotkey()`, wire restart flow |
| `ui/index.html` | Modify | Add Hotkey settings section + key capture modal HTML |
| `ui/app.js` | Modify | Key capture logic, modal open/close, save flow, dynamic hotkey hints |
| `ui/style.css` | Modify | Modal overlay + capture area styles |

---

## Task 1: Make WindowsHotkeyListener Accept Configurable Keys

**Files:**
- Modify: `src/windows_hotkey.py`

- [ ] **Step 1: Add VK code mapping and new constructor parameter**

Add the key-to-VK mapping dict and update `__init__` to accept `keys=None` (defaults to `["win", "ctrl"]`).

```python
# Add after VK_SPACE = 0x20 (line 34)

# ── Key identifier → VK code mapping ────────────────────────────────
KEY_TO_VK = {
    "win":   [0x5B, 0x5C],          # VK_LWIN, VK_RWIN
    "ctrl":  [0x11, 0xA2, 0xA3],    # VK_CONTROL, VK_LCONTROL, VK_RCONTROL
    "alt":   [0x12, 0xA4, 0xA5],    # VK_MENU, VK_LMENU, VK_RMENU
    "shift": [0x10, 0xA0, 0xA1],    # VK_SHIFT, VK_LSHIFT, VK_RSHIFT
}

# Non-modifier function/letter keys
for i in range(1, 25):  # F1-F24
    KEY_TO_VK[f"f{i}"] = [0x70 + i - 1]
for c in "abcdefghijklmnopqrstuvwxyz":
    KEY_TO_VK[c] = [ord(c.upper())]
for d in "0123456789":
    KEY_TO_VK[d] = [ord(d)]

MODIFIER_KEYS = {"win", "ctrl", "alt", "shift"}
DEFAULT_HOTKEY = ["win", "ctrl"]

def _vk_to_key_id(vk):
    """Reverse lookup: VK code → key string ID, or None."""
    for key_id, vk_list in KEY_TO_VK.items():
        if vk in vk_list:
            return key_id
    return None

def hotkey_display(keys):
    """Format key list as display string, e.g. ['win', 'ctrl'] → 'Win + Ctrl'."""
    return " + ".join(k.capitalize() if k in MODIFIER_KEYS else k.upper() for k in keys)
```

- [ ] **Step 2: Update `__init__` to accept and store `keys`**

Replace the `__init__` method:

```python
def __init__(self, on_press, on_release, keys=None):
    self._on_press   = on_press
    self._on_release = on_release
    self._state      = _State.IDLE
    self._running    = False
    self._hook       = None
    self._thread_id  = None

    # Configurable keys
    self._keys = keys or DEFAULT_HOTKEY
    self._key_states = {k: False for k in self._keys}
    self._suppress_keys = set()  # VK codes to suppress on key-up
    self._busy = False

    # Must prevent garbage collection of the callback
    self._hook_proc = HOOKPROC(self._ll_keyboard_proc)
```

- [ ] **Step 3: Rewrite `_ll_keyboard_proc` for generic key tracking**

Replace the hook callback to track configured keys dynamically instead of hardcoded Win/Ctrl:

```python
def _ll_keyboard_proc(self, nCode, wParam, lParam):
    """Called by Windows for every keyboard event system-wide."""
    if nCode >= 0:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_up   = wParam in (WM_KEYUP, WM_SYSKEYUP)

        key_id = _vk_to_key_id(vk)

        # ── Track configured key state ──
        if key_id and key_id in self._key_states:
            if is_down and not self._key_states[key_id]:
                self._key_states[key_id] = True
                self._check_combo_press()
                # Suppress Win/Alt key-down when other combo keys are held
                if key_id in ("win", "alt") and self._all_keys_held():
                    self._suppress_keys.add(vk)
                    return 1  # block
            elif is_down and self._key_states[key_id]:
                # Auto-repeat — suppress if we're suppressing this key
                if vk in self._suppress_keys:
                    return 1
            elif is_up:
                self._key_states[key_id] = False
                self._check_release()
                # Suppress key-up to prevent OS side effects
                if vk in self._suppress_keys:
                    self._suppress_keys.discard(vk)
                    return 1

        # ── Track Space (sticky mode trigger) ──
        elif vk == VK_SPACE and is_down:
            if self._state == _State.PUSH_TO_TALK:
                self._enter_sticky()

    return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)
```

- [ ] **Step 4: Add `_all_keys_held` helper and update `is_combo_active`**

```python
def _all_keys_held(self):
    """Return True when all configured keys are currently held."""
    return all(self._key_states.values())

@property
def is_combo_active(self):
    """Return True when all configured hotkey keys are held."""
    return self._all_keys_held()
```

- [ ] **Step 5: Update `_check_combo_press` and `_check_release` to use generic state**

```python
def _check_combo_press(self):
    """Called when any configured key is pressed."""
    if not self._all_keys_held():
        return

    if self._busy and self._state == _State.IDLE:
        _log("Combo ignored — still processing")
        return

    if self._state == _State.IDLE:
        self._state = _State.PUSH_TO_TALK
        _log(f"{hotkey_display(self._keys)} → PUSH_TO_TALK, start recording")
        self._fire_press()
    elif self._state == _State.STICKY:
        self._state = _State.IDLE
        _log(f"{hotkey_display(self._keys)} → cancel STICKY, stop recording")
        self._fire_release()

def _check_release(self):
    """Called when any configured key is released."""
    if self._state == _State.PUSH_TO_TALK:
        if not self._all_keys_held():
            self._state = _State.IDLE
            _log("Key released → stop PUSH_TO_TALK")
            self._fire_release()
```

- [ ] **Step 6: Update `_poll_fallback` for configurable keys**

```python
def _poll_fallback(self):
    """Fallback: GetAsyncKeyState polling with configurable keys."""
    _log(f"Polling fallback active: {hotkey_display(self._keys)} (30ms interval)")
    while self._running:
        try:
            all_held = all(
                any(_key_down(vk) for vk in KEY_TO_VK.get(k, []))
                for k in self._keys
            )
            space = _key_down(VK_SPACE)

            if self._state == _State.IDLE:
                if all_held:
                    self._state = _State.PUSH_TO_TALK
                    _log(f"[poll] {hotkey_display(self._keys)} → PUSH_TO_TALK")
                    self._fire_press()
            elif self._state == _State.PUSH_TO_TALK:
                if space:
                    self._enter_sticky()
                elif not all_held:
                    self._state = _State.IDLE
                    _log("[poll] released → stop PUSH_TO_TALK")
                    self._fire_release()
            elif self._state == _State.STICKY:
                if all_held:
                    self._state = _State.IDLE
                    _log(f"[poll] {hotkey_display(self._keys)} → cancel STICKY")
                    self._fire_release()
                    time.sleep(0.3)
        except Exception as e:
            _log(f"Poll error: {e}")
        time.sleep(0.03)
```

- [ ] **Step 7: Commit**

```bash
git add src/windows_hotkey.py
git commit -m "feat: make WindowsHotkeyListener accept configurable keys"
```

---

## Task 2: Backend API — Hotkey Config + Listener Restart

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Store listener reference in pipeline**

In `WafflerPipeline.start_hotkey()` (~line 1526), store the listener on self:

```python
def start_hotkey(self):
    """Start the hotkey listener — platform-specific."""
    try:
        # Load configured keys from settings
        sf = DATA_DIR / "settings.json"
        keys = None
        try:
            if sf.exists():
                stored = json.loads(sf.read_text())
                keys = stored.get("hotkey_keys")
        except Exception:
            pass

        if _platform.system() == "Windows":
            _log_to_file("Creating WindowsHotkeyListener...")
            self.hotkey_listener = WindowsHotkeyListener(
                on_press=self.on_hotkey_press,
                on_release=self.on_hotkey_release,
                keys=keys,
            )
        else:
            _log_to_file("Creating SmartHotkeyListener...")
            self.hotkey_listener = SmartHotkeyListener(
                on_press=self.on_hotkey_press,
                on_release=self.on_hotkey_release,
            )
        _log_to_file("Calling hotkey.start()...")
        self.hotkey_listener.start()
        self.hotkey_listener.join()
    except Exception as e:
        _log_to_file(f"start_hotkey CRASHED: {e}")
        import traceback
        traceback.print_exc()
```

- [ ] **Step 2: Add `get_hotkey_config()` to `Api` class**

Add after the `test_hotkey` method (~line 630):

```python
def get_hotkey_config(self) -> dict:
    """Return current hotkey configuration."""
    try:
        stored = self._load_settings_file()
        keys = stored.get("hotkey_keys")
        # Validate keys
        from windows_hotkey import KEY_TO_VK, DEFAULT_HOTKEY, MODIFIER_KEYS, hotkey_display
        if not keys or not isinstance(keys, list):
            keys = DEFAULT_HOTKEY
        # Check all keys are recognized
        for k in keys:
            if k not in KEY_TO_VK:
                _log_to_file(f"Invalid hotkey key '{k}', falling back to default")
                keys = DEFAULT_HOTKEY
                break
        return {"ok": True, "keys": keys, "display": hotkey_display(keys)}
    except Exception as e:
        return {"ok": True, "keys": ["win", "ctrl"], "display": "Win + Ctrl"}
```

- [ ] **Step 3: Add `save_hotkey_config()` to `Api` class**

```python
def save_hotkey_config(self, keys) -> dict:
    """Save hotkey config and restart the listener."""
    try:
        # Parse keys (comes as string from JS)
        if isinstance(keys, str):
            keys = json.loads(keys)
        if not isinstance(keys, list) or len(keys) == 0:
            return {"ok": False, "error": "Invalid keys format"}

        from windows_hotkey import KEY_TO_VK, MODIFIER_KEYS, hotkey_display

        # Validate: all keys recognized
        for k in keys:
            if k not in KEY_TO_VK:
                return {"ok": False, "error": f"Unknown key: {k}"}

        # Validate: at least one modifier
        if not any(k in MODIFIER_KEYS for k in keys):
            return {"ok": False, "error": "At least one modifier key required (Ctrl, Alt, Shift, or Win)"}

        # Validate: max 3 keys
        if len(keys) > 3:
            return {"ok": False, "error": "Maximum 3 keys allowed"}

        # Validate: reject reserved combos
        key_set = set(keys)
        reserved = [
            {"ctrl", "alt"},  # partial match for Ctrl+Alt+Del — block Ctrl+Alt alone
        ]
        if key_set == {"alt"} or key_set == {"win"}:
            return {"ok": False, "error": "Single modifier not allowed"}

        # Save to settings.json
        stored = self._load_settings_file()
        stored["hotkey_keys"] = keys
        self._save_settings_file(stored)
        _log_to_file(f"Hotkey config saved: {keys}")

        # Restart listener if pipeline is running
        if _pipeline and hasattr(_pipeline, 'hotkey_listener') and _pipeline.hotkey_listener:
            _log_to_file("Restarting hotkey listener with new keys...")
            _pipeline.hotkey_listener.stop()

            def _restart():
                import time
                time.sleep(0.3)  # brief wait for old hook to uninstall
                from windows_hotkey import WindowsHotkeyListener
                _pipeline.hotkey_listener = WindowsHotkeyListener(
                    on_press=_pipeline.on_hotkey_press,
                    on_release=_pipeline.on_hotkey_release,
                    keys=keys,
                )
                _log_to_file("New hotkey listener starting...")
                _pipeline.hotkey_listener.start()

            threading.Thread(target=_restart, daemon=True, name="HotkeyRestart").start()

        return {"ok": True, "display": hotkey_display(keys)}
    except Exception as e:
        _log_to_file(f"save_hotkey_config error: {e}")
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 4: Update `test_hotkey()` to return configured display**

```python
def test_hotkey(self) -> dict:
    """Return hotkey configuration info for the current platform."""
    import platform as plat
    is_win = plat.system() == "Windows"
    if is_win:
        config = self.get_hotkey_config()
        display = config.get("display", "Win + Ctrl")
    else:
        display = "Fn (hold)"
    return {
        "ok": True,
        "platform": plat.system(),
        "hotkey": display,
        "mode": "hold",
        "description": (
            f"Hold {display} to record. Release to stop. Hold Space while pressing to lock recording on."
        ) if is_win else (
            "Hold the Fn key to record. Release to stop. "
            "Fn + Space locks recording on — press Fn again to stop."
        ),
    }
```

- [ ] **Step 5: Update wizard call sites to pass configured keys**

In `wizard_start_fn_detection()` (~line 731) and `wizard_start_hotkey_test()` (~line 782), pass keys:

```python
# In wizard_start_fn_detection:
if _platform.system() == "Windows":
    # Load configured keys
    stored = self._load_settings_file()
    keys = stored.get("hotkey_keys")
    self.hotkey_listener = WindowsHotkeyListener(
        on_press=lambda: None,
        on_release=lambda: None,
        keys=keys,
    )

# In wizard_start_hotkey_test:
if _platform.system() == "Windows":
    stored = self._load_settings_file()
    keys = stored.get("hotkey_keys")
    _wizard_hotkey = WindowsHotkeyListener(
        on_press=_wizard_on_press,
        on_release=_wizard_on_release,
        keys=keys,
    )
```

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: hotkey config API with listener restart and wizard integration"
```

---

## Task 3: Settings UI — Hotkey Section + Key Capture Modal

**Files:**
- Modify: `ui/index.html`
- Modify: `ui/style.css`

- [ ] **Step 1: Add Hotkey section HTML in Settings panel**

Insert between the API Keys section (ends line ~157) and Preferences section (starts line ~159) in `index.html`:

```html
<!-- Hotkey Section -->
<div class="settings-section">
  <div class="settings-section-title">⌨️ Hotkey</div>
  <div class="settings-row">
    <div class="settings-row-info">
      <div class="settings-row-label">Recording Hotkey</div>
      <div class="settings-row-desc">Hold to record, release to stop</div>
    </div>
    <div class="settings-row-control" style="gap:10px;">
      <span class="hotkey-badge" id="settingsHotkeyBadge">Win + Ctrl</span>
      <button class="settings-btn accent" onclick="openHotkeyCapture()">Change</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add Key Capture Modal HTML**

Add before the closing `</div><!-- .app -->` at the end of `index.html` (line ~239):

```html
<!-- Hotkey Capture Modal -->
<div class="hotkey-modal-overlay" id="hotkeyModal" style="display:none" onclick="closeHotkeyCapture()">
  <div class="hotkey-modal" onclick="event.stopPropagation()">
    <div class="hotkey-modal-icon">⌨️</div>
    <div class="hotkey-modal-title">Change Hotkey</div>
    <div class="hotkey-modal-subtitle">Press your new key combination...</div>
    <div class="hotkey-capture-box" id="hotkeyCaptureBox">
      <div class="hotkey-capture-keys" id="hotkeyCaptureKeys">Win + Ctrl</div>
      <div class="hotkey-capture-hint">Hold your desired keys together</div>
    </div>
    <div class="hotkey-modal-error" id="hotkeyError" style="display:none"></div>
    <div class="hotkey-modal-buttons">
      <button class="hotkey-btn secondary" onclick="closeHotkeyCapture()">Cancel</button>
      <button class="hotkey-btn secondary" onclick="resetHotkeyDefault()">Reset Default</button>
      <button class="hotkey-btn primary" id="hotkeyModalSave" onclick="saveHotkeyCapture()">Save</button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add modal CSS**

Append to `ui/style.css`:

```css
/* ── Hotkey Capture Modal ─────────────────────────────────────── */
.hotkey-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}
.hotkey-modal {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 28px 36px;
  text-align: center;
  max-width: 380px;
  width: 90%;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
}
.hotkey-modal-icon { font-size: 28px; margin-bottom: 10px; }
.hotkey-modal-title { color: var(--text-primary); font-size: 18px; font-weight: 600; margin-bottom: 6px; }
.hotkey-modal-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 20px; }
.hotkey-capture-box {
  background: var(--bg-hover);
  border: 2px solid var(--accent);
  border-radius: var(--radius-md);
  padding: 16px 24px;
  margin-bottom: 18px;
}
.hotkey-capture-keys {
  color: var(--accent);
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 1px;
}
.hotkey-capture-hint { color: var(--text-muted); font-size: 11px; margin-top: 4px; }
.hotkey-modal-error {
  color: var(--red);
  font-size: 12px;
  margin-bottom: 12px;
}
.hotkey-modal-buttons { display: flex; gap: 10px; justify-content: center; }
.hotkey-btn {
  border: none;
  border-radius: var(--radius-sm);
  padding: 8px 20px;
  font-size: 13px;
  cursor: pointer;
}
.hotkey-btn.secondary { background: var(--border); color: var(--text-secondary); }
.hotkey-btn.secondary:hover { background: var(--bg-hover); }
.hotkey-btn.primary { background: var(--accent); color: var(--bg-base); font-weight: 600; }
.hotkey-btn.primary:hover { background: var(--accent-dim); }
.settings-btn.accent { background: var(--accent); color: var(--bg-base); font-weight: 600; }
.settings-btn.accent:hover { background: var(--accent-dim); }
```

- [ ] **Step 4: Commit**

```bash
git add ui/index.html ui/style.css
git commit -m "feat: add hotkey settings section and key capture modal UI"
```

---

## Task 4: Frontend Key Capture Logic

**Files:**
- Modify: `ui/app.js`

- [ ] **Step 1: Add key capture state and mapping**

Add near the top of `app.js` after the state variables (~line 8):

```javascript
// ── Hotkey capture state ──────────────────────────────────────────
let _capturedKeys = new Set();
let _lastCapturedKeys = [];
let _currentHotkeyKeys = ["win", "ctrl"];

const JS_KEY_TO_ID = {
  "Control": "ctrl", "Alt": "alt", "Shift": "shift",
  "Meta": "win", "OS": "win",
};
const MODIFIER_IDS = new Set(["ctrl", "alt", "shift", "win"]);

function jsKeyToId(e) {
  if (JS_KEY_TO_ID[e.key]) return JS_KEY_TO_ID[e.key];
  if (e.code.startsWith("Key")) return e.code.slice(3).toLowerCase();
  if (e.code.startsWith("Digit")) return e.code.slice(5);
  if (e.code.startsWith("F") && !isNaN(e.code.slice(1))) return e.code.toLowerCase();
  return null;
}

function hotkeyDisplayStr(keys) {
  return keys.map(k => MODIFIER_IDS.has(k) ? k.charAt(0).toUpperCase() + k.slice(1) : k.toUpperCase()).join(" + ");
}
```

- [ ] **Step 2: Add modal open/close and key capture handlers**

```javascript
function openHotkeyCapture() {
  _capturedKeys.clear();
  _lastCapturedKeys = [..._currentHotkeyKeys];
  document.getElementById("hotkeyCaptureKeys").textContent = hotkeyDisplayStr(_lastCapturedKeys);
  document.getElementById("hotkeyError").style.display = "none";
  document.getElementById("hotkeyModal").style.display = "flex";
  document.addEventListener("keydown", _onCaptureKeyDown);
  document.addEventListener("keyup", _onCaptureKeyUp);
}

function closeHotkeyCapture() {
  document.getElementById("hotkeyModal").style.display = "none";
  document.removeEventListener("keydown", _onCaptureKeyDown);
  document.removeEventListener("keyup", _onCaptureKeyUp);
  _capturedKeys.clear();
}

function _onCaptureKeyDown(e) {
  e.preventDefault();
  e.stopPropagation();
  const id = jsKeyToId(e);
  if (!id) return;
  _capturedKeys.add(id);
  _lastCapturedKeys = [..._capturedKeys];
  document.getElementById("hotkeyCaptureKeys").textContent = hotkeyDisplayStr(_lastCapturedKeys);
  document.getElementById("hotkeyError").style.display = "none";
}

function _onCaptureKeyUp(e) {
  e.preventDefault();
  e.stopPropagation();
  const id = jsKeyToId(e);
  if (id) _capturedKeys.delete(id);
  // _lastCapturedKeys persists — shows what was held
}

function resetHotkeyDefault() {
  _lastCapturedKeys = ["win", "ctrl"];
  _capturedKeys.clear();
  document.getElementById("hotkeyCaptureKeys").textContent = "Win + Ctrl";
  document.getElementById("hotkeyError").style.display = "none";
}

async function saveHotkeyCapture() {
  const keys = _lastCapturedKeys;
  if (!keys.length) return;

  // Client-side validation
  if (!keys.some(k => MODIFIER_IDS.has(k))) {
    document.getElementById("hotkeyError").textContent = "At least one modifier key required (Ctrl, Alt, Shift, or Win)";
    document.getElementById("hotkeyError").style.display = "block";
    return;
  }
  if (keys.length > 3) {
    document.getElementById("hotkeyError").textContent = "Maximum 3 keys allowed";
    document.getElementById("hotkeyError").style.display = "block";
    return;
  }

  try {
    const result = await window.pywebview.api.save_hotkey_config(JSON.stringify(keys));
    if (result.ok) {
      _currentHotkeyKeys = keys;
      closeHotkeyCapture();
      // Update all hotkey displays
      loadHotkeyConfig();
    } else {
      document.getElementById("hotkeyError").textContent = result.error;
      document.getElementById("hotkeyError").style.display = "block";
    }
  } catch (e) {
    document.getElementById("hotkeyError").textContent = "Failed to save";
    document.getElementById("hotkeyError").style.display = "block";
  }
}
```

- [ ] **Step 3: Add `loadHotkeyConfig()` to populate all displays from backend**

```javascript
async function loadHotkeyConfig() {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    const config = await window.pywebview.api.get_hotkey_config();
    if (config.ok) {
      _currentHotkeyKeys = config.keys;
      const display = config.display;

      // Settings badge
      const settingsBadge = document.getElementById("settingsHotkeyBadge");
      if (settingsBadge) settingsBadge.textContent = display;

      // Sidebar hint
      const sidebarBadge = document.getElementById("hotkeyHint");
      if (sidebarBadge) sidebarBadge.textContent = display;

      // Home page badge
      const badge = document.getElementById("hotkeyBadge");
      if (badge) badge.textContent = display;

      // Empty state hint
      const emptyHint = document.getElementById("emptyHint");
      if (emptyHint) emptyHint.innerHTML = `Hold <strong>${display}</strong> to record`;
    }
  } catch (e) {
    console.error("loadHotkeyConfig error:", e);
  }
}
```

- [ ] **Step 4: Update `updateHotkeyHint()` to use `loadHotkeyConfig()`**

Replace the existing `updateHotkeyHint()` function (lines 49-68 in `app.js`):

```javascript
function updateHotkeyHint() {
  const isWin = navigator.userAgent.includes('Windows');
  if (isWin) {
    // Load from backend config (async, will update when ready)
    loadHotkeyConfig();
  } else {
    // macOS - Fn key, not configurable
    const badge = document.getElementById('hotkeyBadge');
    const sidebarBadge = document.getElementById('hotkeyHint');
    const label = document.getElementById('hotkeyLabel');
    const emptyHint = document.getElementById('emptyHint');
    if (badge) badge.textContent = 'Fn';
    if (sidebarBadge) sidebarBadge.textContent = 'Fn';
    if (label) label.textContent = 'Tap to start/stop';
    if (emptyHint) emptyHint.innerHTML = 'Press <strong>Fn</strong> to start recording';
  }
}
```

- [ ] **Step 5: Call `loadHotkeyConfig()` on pywebviewready**

In the existing `pywebviewready` handler (~line 25), add:

```javascript
window.addEventListener('pywebviewready', () => {
  refreshAll();
  updateDateLabel();
  loadHotkeyConfig();  // <-- add this line
});
```

- [ ] **Step 6: Commit**

```bash
git add ui/app.js
git commit -m "feat: key capture modal logic and dynamic hotkey display"
```

---

## Task 5: App Window Icon

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Pass icon to `webview.create_window()`**

In `main()` (~line 1730), update the `create_window` call:

```python
# Resolve icon path (works for both dev and frozen/PyInstaller builds)
icon_path = PROJECT_ROOT / "icon.ico"
if not icon_path.exists():
    icon_path = None  # pywebview will use default

window = webview.create_window(
    title="Waffler",
    url=str(html_path),
    width=1100,
    height=780,
    min_size=(900, 640),
    resizable=True,
    background_color="#0d0d0f",
    js_api=api,
    frameless=False,
    easy_drag=False,
)
```

**Note:** pywebview on Windows does not accept an `icon` parameter in `create_window`. Instead, set the icon after window creation using the Win32 API. Add this after `set_window(window)`:

```python
# Set window icon on Windows
if _platform.system() == "Windows" and icon_path and icon_path.exists():
    def _set_window_icon():
        import time
        time.sleep(1)  # wait for window to be created
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            icon_handle = ctypes.windll.user32.LoadImageW(
                None, str(icon_path), 1, 0, 0, 0x00000010  # IMAGE_ICON, LR_LOADFROMFILE
            )
            if icon_handle:
                ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, icon_handle)  # WM_SETICON, ICON_SMALL
                ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, icon_handle)  # WM_SETICON, ICON_BIG
                _log_to_file("Window icon set successfully")
        except Exception as e:
            _log_to_file(f"Failed to set window icon: {e}")
    threading.Thread(target=_set_window_icon, daemon=True).start()
```

- [ ] **Step 2: Commit**

```bash
git add app.py
git commit -m "feat: set Waffler icon on window title bar"
```

---

## Task 6: Manual Smoke Test

- [ ] **Step 1: Launch the app**

```bash
cd C:/Users/james/waffler && python app.py
```

- [ ] **Step 2: Verify default behavior**

- App launches with Waffler icon in title bar (not Python default)
- Sidebar shows "Win + Ctrl" badge
- Hold Win+Ctrl → recording starts, release → stops
- Space during hold → sticky mode works

- [ ] **Step 3: Test hotkey customization**

- Go to Settings → Hotkey section shows "Win + Ctrl" with "Change" button
- Click "Change" → modal appears
- Press Ctrl+Shift → displays "Ctrl + Shift" live
- Release keys → display persists
- Click Save → modal closes, sidebar badge updates to "Ctrl + Shift"
- Hold Ctrl+Shift → recording starts
- Old Win+Ctrl no longer triggers recording

- [ ] **Step 4: Test Reset Default**

- Open Change modal → click "Reset Default" → shows "Win + Ctrl"
- Save → back to default

- [ ] **Step 5: Test validation**

- Try saving just a letter key (no modifier) → error shown
- Try saving 4+ keys → error shown

- [ ] **Step 6: Commit final state**

```bash
git add -A
git commit -m "chore: hotkey customization implementation complete"
```
