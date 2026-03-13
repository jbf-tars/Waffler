# 🎙️ Waffler - Quick Start (5 minutes)

## Step 1: Grant Accessibility Permission (1 min)

1. **System Settings** → **Privacy & Security** → **Accessibility**
2. Click **+** and add **Terminal**
3. Toggle it **ON**

## Step 2: Run the App (1 min)

```bash
cd /Users/tars/Desktop/waffler
./run.sh
```

You should see:
```
✅ Ready! Press and hold Cmd+Shift+Space to record.
```

## Step 3: Test with Voice (2 mins)

1. **Press & hold:** Cmd+Shift+Space
2. **Speak:** Say anything (e.g., "Hello world")
3. **Release:** Let go of the hotkey
4. **Wait:** ~2-3 seconds for processing
5. **Check clipboard:** Cmd+V to paste your styled text!

---

## ✅ Success Indicators

- ✅ Hotkey works (console shows "🎤 RECORDING")
- ✅ Microphone captures sound
- ✅ Text appears in console log
- ✅ Notification says "Ready!"
- ✅ Text is in your clipboard

---

## ❌ Issues?

- **"This process is not trusted"** → Grant accessibility permission (Step 1)
- **No sound recorded** → Try holding hotkey longer (at least 1 sec)
- **App crashes** → Check console output for errors
- **Text not in clipboard** → Run component tests: `python test_components.py`

---

## 📋 Full Testing?

See **TESTING.md** for comprehensive guide with troubleshooting, latency measurements, and edge cases.

---

## 🎯 What's Next?

After successful voice test:
1. Measure latency (it logs timing breakdown)
2. Try different phrasings to test robustness
3. That's Week 1 complete! 🎉

---

**Questions?** Check TESTING.md or README.md for full documentation.
