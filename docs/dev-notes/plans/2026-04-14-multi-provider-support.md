# Multi-Provider LLM Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Gemini (and extensible support for future providers) as an LLM styling option alongside Groq and OpenAI, in both the setup wizard and settings panel.

**Architecture:** Add a Gemini provider pill to the existing wizard Step 3 and settings panel. The styling backend (`style_openai.py`) gains a Gemini path using the `google-genai` SDK. Transcription stays Groq/OpenAI Whisper only (Gemini doesn't offer a competitive Whisper alternative). Provider selection is driven by which API keys are present, with Groq > Gemini > OpenAI priority for styling.

**Tech Stack:** Python `google-genai` SDK, existing Groq SDK, existing OpenAI SDK, pywebview JS API.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `ui/index.html` | Modify | Add Gemini pill + key input to wizard Step 3 and settings panel |
| `ui/app.js` | Modify | Add Gemini validation, switchProvider update, settings save/load |
| `src/style_openai.py` | Modify | Add Gemini styling path with `google-genai` SDK |
| `app.py` | Modify | Add `validate_gemini_key()` API, update pipeline init, update `get_settings()` |
| `requirements_windows.txt` | Modify | Add `google-genai` dependency |
| `requirements.txt` | Modify | Add `google-genai` dependency |
| `Waffler_windows.spec` | Modify | Add `google-genai` to hidden imports if needed |
| `Waffler_mac.spec` | Modify | Add `google-genai` to hidden imports if needed |

---

### Task 1: Add Gemini SDK dependency

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements_windows.txt`

- [ ] **Step 1: Add google-genai to requirements.txt**

Add `google-genai>=1.0.0` to `requirements.txt` (Mac) — this is the modern Google AI SDK.

- [ ] **Step 2: Add google-genai to requirements_windows.txt**

Add `google-genai>=1.0.0` to `requirements_windows.txt`.

- [ ] **Step 3: Install locally**

Run: `pip install google-genai`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt requirements_windows.txt
git commit -m "feat: add google-genai SDK dependency for Gemini support"
```

---

### Task 2: Add Gemini styling backend

**Files:**
- Modify: `src/style_openai.py`

- [ ] **Step 1: Add Gemini SDK import at top of file**

After the Groq import block (lines 10-14), add:

```python
_gemini_mod = None
try:
    from google import genai
    _gemini_mod = genai
except ImportError:
    pass
```

- [ ] **Step 2: Update `__init__` to accept gemini_api_key**

Change the `__init__` signature to:

```python
def __init__(self, api_key: str = "", model: str = "gpt-4o-mini",
             max_tokens: int = 1024, prompt_style: str = "normal",
             groq_api_key: str = "", gemini_api_key: str = ""):
```

Add after `self.groq_api_key = groq_api_key`:

```python
self.gemini_api_key = gemini_api_key
self._gemini_client = None
self._use_gemini = False
```

- [ ] **Step 3: Update provider selection logic in `__init__`**

Replace the existing provider selection block (lines 32-39) with:

```python
# Priority 1: Groq for styling if available
if groq_api_key and _groq_mod:
    self._groq_client = _groq_mod.Groq(api_key=groq_api_key)
    self._use_groq = True
    self._groq_model = "meta-llama/llama-4-scout-17b-16e-instruct"
    print(f"Styling: Groq {self._groq_model}")
# Priority 2: Gemini
elif gemini_api_key and _gemini_mod:
    self._gemini_client = _gemini_mod.Client(api_key=gemini_api_key)
    self._use_gemini = True
    self._gemini_model = "gemini-2.5-flash"
    print(f"Styling: Gemini {self._gemini_model}")
else:
    print(f"Styling: OpenAI {model}")
```

- [ ] **Step 4: Add `_style_gemini` method**

Add after the `_style_groq` method:

```python
def _style_gemini(self, prompt: str, start_time: float):
    """Style using Gemini — fast and free tier available."""
    system_msg = (
        "You are a voice-to-text formatter. Output ONLY the final cleaned/formatted text. "
        "Follow ALL formatting rules in the user prompt exactly — including paragraph breaks, "
        "email structure, numbered lists, and bullet points. Preserve line breaks in your output. "
        "NEVER output your classification, reasoning, labels, or any meta-commentary. "
        "Do NOT prefix your output with things like 'This is a COMMAND' or 'Output:'. "
        "Just return the cleaned text directly."
        + getattr(self, '_vocab_system_extra', '')
    )
    try:
        response = self._gemini_client.models.generate_content(
            model=self._gemini_model,
            contents=prompt,
            config={
                "system_instruction": system_msg,
                "temperature": 0.1,
                "max_output_tokens": 512,
            },
        )
        styled = response.text.strip()
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            raise RuntimeError(f"RATE_LIMIT: Gemini rate limit hit — {error_msg[:100]}")
        elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
            raise RuntimeError(f"CONNECTION: Gemini connection failed — {error_msg[:100]}")
        raise
    styled = self._fix_mid_sentence_caps(styled)
    latency = (time.time() - start_time) * 1000
    # Gemini usage tracking — token counts from response metadata
    input_tokens = getattr(response, 'usage_metadata', None)
    return styled, {
        "input_tokens": input_tokens.prompt_token_count if input_tokens else 0,
        "output_tokens": input_tokens.candidates_token_count if input_tokens else 0,
        "api_used": True,
        "provider": "gemini",
    }
```

- [ ] **Step 5: Update `style()` method to include Gemini in the fallback chain**

In the `style()` method, after the Groq try/except block and before the OpenAI fallback, add:

```python
# Priority 2: Try Gemini
if self._use_gemini:
    try:
        return self._style_gemini(prompt, start_time)
    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        print(f"Gemini styling failed ({e}), falling back to OpenAI")
        from pathlib import Path
        from datetime import datetime
        try:
            log_file = Path.home() / ".waffler-hosted" / "app.log"
            with open(log_file, "a") as f:
                ts = datetime.now().strftime("%H:%M:%S")
                f.write(f"{ts}  [styling] Gemini FAILED: {e}\n")
                f.write(f"{ts}  [styling] {err_detail}\n")
        except Exception:
            pass
        if not self.client:
            return self._basic_clean(transcript), {"input_tokens": 0, "output_tokens": 0, "api_used": False}
```

- [ ] **Step 6: Commit**

```bash
git add src/style_openai.py
git commit -m "feat: add Gemini as styling provider with fallback chain"
```

---

### Task 3: Add Gemini key validation to backend API

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add `validate_gemini_key()` method to the Api class**

Add after `validate_groq_key()` (around line 608):

```python
def validate_gemini_key(self, api_key: str) -> dict:
    """Validate a Gemini API key by listing models."""
    api_key = (api_key or "").strip()
    if not api_key:
        return {"ok": False, "error": "No API key provided"}
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        # Quick validation — list models
        list(client.models.list())
        # Key is valid — persist it
        self._update_env_var("GEMINI_API_KEY", api_key)
        os.environ["GEMINI_API_KEY"] = api_key
        return {"ok": True, "message": "Gemini key is valid!"}
    except ImportError:
        return {"ok": False, "error": "Gemini SDK not installed"}
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "API_KEY_INVALID" in error_msg or "invalid" in error_msg.lower():
            return {"ok": False, "error": "Invalid Gemini API key"}
        elif "403" in error_msg:
            return {"ok": False, "error": "Access denied — check your Gemini API key permissions"}
        elif "429" in error_msg:
            return {"ok": False, "error": "Rate limited — try again shortly"}
        else:
            return {"ok": False, "error": f"Connection error: {error_msg[:100]}"}
```

- [ ] **Step 2: Update pipeline initialization to pass gemini_api_key**

Find where `OpenAIStyler` is created (around line 1475) and add `gemini_api_key`:

```python
gemini_key = os.environ.get("GEMINI_API_KEY", "")
self.styler = OpenAIStyler(
    api_key=openai_key,
    model="gpt-4o-mini",
    groq_api_key=groq_key,
    gemini_api_key=gemini_key,
)
```

- [ ] **Step 3: Update `get_settings()` to expose Gemini key status and active backend**

In `get_settings()`, add:
- `"gemini_key_set"` boolean (check if `GEMINI_API_KEY` env var is set)
- Update the `backend_info` string to mention Gemini if active

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: add Gemini key validation and pipeline integration"
```

---

### Task 4: Add Gemini to wizard Step 3 UI

**Files:**
- Modify: `ui/index.html`

- [ ] **Step 1: Add Gemini pill button**

In the `provider-pills` div (line 413-420), add a Gemini pill between Groq and OpenAI:

```html
<div class="provider-pills">
  <button class="pill-button active" data-provider="groq" onclick="switchProvider('groq')">
    Groq
  </button>
  <button class="pill-button" data-provider="gemini" onclick="switchProvider('gemini')">
    Gemini
  </button>
  <button class="pill-button" data-provider="openai" onclick="switchProvider('openai')">
    OpenAI
  </button>
</div>
```

- [ ] **Step 2: Add Gemini key input field**

Add after the Groq field div (after line 441), before the OpenAI field:

```html
<!-- Gemini Field -->
<div id="geminiField" class="provider-field">
  <div class="provider-container">
    <div class="provider-header">
      <div class="provider-icon">✨</div>
      <div class="provider-title">
        <h3>Gemini API Key</h3>
        <p>Google's fast and free tier available</p>
      </div>
    </div>
    <div class="api-key-input-group">
      <input type="password" id="wizGeminiKeyInput3" class="api-key-input" placeholder="AIza..." autocomplete="off" spellcheck="false">
      <button class="btn-toggle-visibility" onclick="toggleKeyVisibility('wizGeminiKeyInput3')">👁️</button>
    </div>
    <div class="provider-hint">
      Get your free key: <a href="#" onclick="pywebview.api.open_url('https://aistudio.google.com/apikey'); return false;">aistudio.google.com/apikey →</a>
    </div>
    <div class="wizard-validation" id="wizGeminiValidation3"></div>
  </div>
</div>
```

- [ ] **Step 3: Commit**

```bash
git add ui/index.html
git commit -m "feat: add Gemini provider pill and key input to wizard"
```

---

### Task 5: Add Gemini to wizard JS logic

**Files:**
- Modify: `ui/app.js`

- [ ] **Step 1: Add Gemini validation state variable**

Near line 1294, add:

```javascript
let _wizardGeminiKeyValidated = false;
```

- [ ] **Step 2: Update `wizUpdateNextButton` to accept Gemini validation**

Change line 1466 from:

```javascript
case 3: btn.disabled = !(_wizardGroqKeyValidated || _wizardApiKeyValidated); break;
```

to:

```javascript
case 3: btn.disabled = !(_wizardGroqKeyValidated || _wizardGeminiKeyValidated || _wizardApiKeyValidated); break;
```

- [ ] **Step 3: Add Gemini key input listener**

In the `attachApiKeyListeners()` function, after the OpenAI key input listener block (after line 1587), add:

```javascript
// ── Gemini key input ──
const geminiInp = document.getElementById('wizGeminiKeyInput3');
if (geminiInp) {
  geminiInp.addEventListener('input', () => {
    _wizardGeminiKeyValidated = false;
    wizUpdateNextButton();
    const val = geminiInp.value.trim();
    const v = document.getElementById('wizGeminiValidation3');
    if (!val) { v.textContent = ''; v.className = 'wizard-validation'; return; }
    if (!val.startsWith('AIza')) { v.textContent = 'Key should start with AIza'; v.className = 'wizard-validation error'; return; }
    if (val.length < 20) { v.textContent = 'Key seems too short...'; v.className = 'wizard-validation error'; return; }
    clearTimeout(_wizGeminiTimer);
    v.textContent = 'Validating...';
    v.className = 'wizard-validation loading';
    _wizGeminiTimer = setTimeout(() => wizValidateGeminiKey(val), 800);
  });
  geminiInp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      clearTimeout(_wizGeminiTimer);
      const val = geminiInp.value.trim();
      if (val.startsWith('AIza') && val.length >= 20) wizValidateGeminiKey(val);
    }
  });
}
```

Also add the timer variable near the other timer declarations:

```javascript
let _wizGeminiTimer;
```

- [ ] **Step 4: Add Gemini validation function**

After `wizValidateApiKey()`, add:

```javascript
async function wizValidateGeminiKey(key) {
  const v = document.getElementById('wizGeminiValidation3');
  v.textContent = 'Validating with Gemini...';
  v.className = 'wizard-validation loading';
  try {
    const r = await pywebview.api.validate_gemini_key(key);
    if (r.ok) {
      v.textContent = 'Gemini key is valid!';
      v.className = 'wizard-validation success';
      _wizardGeminiKeyValidated = true;
    } else {
      v.textContent = r.error || 'Invalid key';
      v.className = 'wizard-validation error';
      _wizardGeminiKeyValidated = false;
    }
  } catch(e) {
    v.textContent = 'Failed to validate — check your internet connection';
    v.className = 'wizard-validation error';
    _wizardGeminiKeyValidated = false;
  }
  wizUpdateNextButton();
}
```

- [ ] **Step 5: Update `switchProvider()` to handle Gemini**

Replace the existing `switchProvider()` function:

```javascript
function switchProvider(provider) {
    // Update button states
    document.querySelectorAll('.pill-button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.provider === provider);
    });

    // Update field visibility
    const fields = ['groqField', 'geminiField', 'openaiField'];
    const providerFieldMap = { groq: 'groqField', gemini: 'geminiField', openai: 'openaiField' };

    fields.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('active', id === providerFieldMap[provider]);
    });

    // Save preference to localStorage
    localStorage.setItem('preferredProvider', provider);
}
```

- [ ] **Step 6: Commit**

```bash
git add ui/app.js
git commit -m "feat: add Gemini validation and provider switching to wizard JS"
```

---

### Task 6: Add Gemini to settings panel

**Files:**
- Modify: `ui/index.html`
- Modify: `ui/app.js`

- [ ] **Step 1: Add Gemini API Key row to settings panel**

In `ui/index.html`, after the Groq API Key settings row (after line 140), add:

```html
<div class="settings-row">
  <div class="settings-row-info">
    <div class="settings-row-label">Gemini API Key</div>
    <div class="settings-row-desc">Google's fast LLM — aistudio.google.com</div>
  </div>
  <div class="settings-row-control stacked">
    <input type="password" id="geminiKeyInput" class="settings-input" placeholder="AIza…">
    <button class="settings-btn" onclick="saveGeminiKey()">Save</button>
  </div>
</div>
```

- [ ] **Step 2: Add `saveGeminiKey()` function in app.js**

Add near `saveGroqKey()` and `saveApiKey()`:

```javascript
async function saveGeminiKey() {
  const inp = document.getElementById('geminiKeyInput');
  const key = inp.value.trim();
  if (!key) return;
  try {
    const r = await pywebview.api.validate_gemini_key(key);
    if (r.ok) {
      inp.value = '';
      inp.placeholder = 'AIza…••••• (saved)';
      loadSettings();
    } else {
      alert(r.error || 'Invalid key');
    }
  } catch(e) {
    alert('Failed to validate key');
  }
}
```

- [ ] **Step 3: Update `loadSettings()` to show Gemini key status**

In the `loadSettings()` function, add handling for the Gemini key input similar to the existing Groq/OpenAI pattern — populate placeholder if key is set.

- [ ] **Step 4: Commit**

```bash
git add ui/index.html ui/app.js
git commit -m "feat: add Gemini API key to settings panel"
```

---

### Task 7: Update PyInstaller specs

**Files:**
- Modify: `Waffler_windows.spec`
- Modify: `Waffler_mac.spec`

- [ ] **Step 1: Check if google-genai needs hidden imports**

Run: `python -c "from google import genai; print('OK')"` — if it works, check if PyInstaller can find it.

- [ ] **Step 2: Add hidden imports if needed**

Add `'google.genai'` to the `hiddenimports` list in both spec files if PyInstaller doesn't auto-detect it.

- [ ] **Step 3: Commit**

```bash
git add Waffler_windows.spec Waffler_mac.spec
git commit -m "feat: add google-genai to PyInstaller hidden imports"
```

---

### Task 8: Final integration test and release

- [ ] **Step 1: Test locally on Windows**

1. Run `python app.py` from source
2. Complete wizard with a Gemini key
3. Verify transcription + styling works end-to-end
4. Verify switching providers in settings works

- [ ] **Step 2: Verify fallback chain**

1. Set only Gemini key → should use Gemini for styling
2. Set both Groq + Gemini → should use Groq (higher priority)
3. Kill internet mid-request → should show error toast, raw transcript in clipboard

- [ ] **Step 3: Push and tag release**

```bash
git push
git tag v3.7.0
git push origin v3.7.0
```

---

## Provider Priority (after implementation)

**Styling:** Groq → Gemini → OpenAI → basic cleanup (no API)
**Transcription:** Groq Whisper → Local Whisper → OpenAI Whisper (unchanged)

## Notes

- Gemini API keys start with `AIza` (Google API key format)
- Free tier: 15 requests/minute, 1M tokens/day — plenty for voice-to-text
- Model: `gemini-2.5-flash` — fast, cheap, good at instruction following
- The `google-genai` SDK is the modern replacement for `google-generativeai` — uses `genai.Client()` pattern
