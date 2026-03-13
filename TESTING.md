# Waffler Testing Guide

## ⚠️ Prerequisite: Grant Accessibility Permission

Waffler needs permission to monitor keyboard input. This is required for the Cmd+Shift+Space hotkey to work.

### Step 1: Grant Permission (One-time setup)

1. Open **System Settings**
2. Go to **Privacy & Security**
3. Scroll down and click **Accessibility**
4. Click the **+** button to add an app
5. Navigate to and select **Terminal** (or the Python app you're using)
6. Toggle it **ON**
7. Close System Settings

### Step 2: Verify Permission Granted

The app will log a message when starting:
- ✅ If it says "⌨️  Hotkey listener started: cmd + shift + space" → Permission is working!
- ❌ If it says "This process is not trusted! Input event monitoring will not be possible..." → Permission needs to be granted

---

## 🚀 Running Waffler

### Option 1: Quick Run (Recommended)

```bash
cd /Users/tars/Desktop/waffler
./run.sh
```

### Option 2: Manual Setup + Run

```bash
# First time only
cd /Users/tars/Desktop/waffler
./setup.sh

# Then run
source venv/bin/activate
python main.py
```

---

## 🎤 Testing the App

### Basic Hotkey Test (No Voice)

1. Start the app with `./run.sh`
2. You should see: `✅ Ready! Press and hold Cmd+Shift+Space to record.`
3. Try pressing and **holding** Cmd+Shift+Space for 2 seconds
4. **Expected output:**
   ```
   ============================================================
   🎤 RECORDING - Hold hotkey and speak...
   ============================================================
   
   🛑 PROCESSING - Transcribing and styling...
   ```
5. Release the hotkey

---

### Voice Test (Full Pipeline)

1. Start the app: `./run.sh`
2. **Press and hold:** Cmd+Shift+Space
3. **Speak clearly:** Try something like: "Hello, this is a test"
4. **Release the hotkey** when done
5. **Wait for processing** (~2-3 seconds)
6. **Expected notification:** macOS notification saying "Ready!"
7. **Check clipboard:** Your styled text should be ready to paste

#### Examples to try:

- **Voice input:** "Turn off the lights"  
  **Expected output:** Something like "Turn off the lights" (styled/cleaned up)

- **Voice input:** "Send email to John about the meeting"  
  **Expected output:** "Send email to John about the meeting" (or similar)

- **Voice input:** "Create a reminder to buy milk"  
  **Expected output:** "Create a reminder to buy milk"

---

## 📊 What to Measure

### Latency Breakdown

The app logs timing for each step. Look for:

```
📊 Audio duration: 2.50s
📡 Transcribing... (~1.4s expected)
✓ Transcript (XXXms): [your speech here]

🤖 Styling... (~0.5-1.0s expected)
✓ Command (XXXms): [styled output]

================
✅ READY (XXXms total)
   • Transcribe: XXXms
   • Style: XXXms
```

**Performance targets:**
- **Deepgram STT:** ~1.4s (already verified)
- **MiniMax styling:** <1.5s
- **Total:** <3.5s (current target)

---

## 🧪 Running Component Tests

If you want to verify everything without needing accessibility permission:

```bash
cd /Users/tars/Desktop/waffler
source venv/bin/activate
python test_components.py
```

**Output should show:**
```
✓ PASS: Imports
✓ PASS: Configuration
✓ PASS: Components
✓ PASS: Clipboard
✓ PASS: API Connectivity

5/5 tests passed
```

---

## 🐛 Troubleshooting

### "This process is not trusted! Input event monitoring will not be possible"

**Solution:** Grant accessibility permission (see above)

### "DEEPGRAM_API_KEY environment variable not set"

**Solution:** Create `.env` file with:
```
DEEPGRAM_API_KEY=cadea590175c43a3e6fa6d98ce59ee1f43e0e3a0
MINIMAX_API_KEY=sk-api-vpsK-or-TxHP85l_P2RjRCAANN-Kg04Nq1oTu3ITxv8PQYQADHHtehs6IBCz6zrmrYig0XBRa6mfdaQRQLc5jpgmiL9i50Ki9anKlPsE5jd-r4bnaxtMric
```

### "No audio recorded"

**Causes:**
1. Hotkey not working → Grant accessibility permission
2. Microphone not working → Check System Settings → Sound → Input
3. Held hotkey too briefly → Try holding for at least 1 second

### "API error: 401 Unauthorized"

**Solution:** API keys might be expired or invalid. Contact support.

### App exits after pressing hotkey

**Solution:** Check the console output for error messages. Common causes:
- Microphone permission needed → Grant in System Settings → Privacy & Security → Microphone
- API key invalid → Verify .env file

---

## 📋 Checklist for Full Testing

- [ ] App starts without errors
- [ ] Cmd+Shift+Space hotkey is detected (no "not trusted" warning)
- [ ] Can press and hold hotkey without crashing
- [ ] Microphone records audio when holding hotkey
- [ ] Deepgram transcribes speech correctly
- [ ] MiniMax styles the transcript
- [ ] Text appears in clipboard
- [ ] macOS notification shows "Ready!"
- [ ] Total latency is <3.5 seconds
- [ ] Multiple uses work without crashes

---

## 📝 Reporting Issues

If something doesn't work:

1. **Capture the full console output** when running:
   ```bash
   ./run.sh 2>&1 | tee waffler_debug.log
   ```

2. **Record:**
   - What you said
   - What the app output
   - What you expected
   - Any error messages

3. **Send to:** Include the debug.log file

---

## 🎉 Success Criteria

✅ Full end-to-end test passes:
1. Press Cmd+Shift+Space
2. Speak clearly
3. Release hotkey
4. See transcript in console
5. See styled output
6. Text appears in clipboard
7. Notification shown
8. Latency <3.5s

---

## 🚀 Next Steps (After Testing)

If everything works:
1. Measure actual latency breakdown
2. Test edge cases (background noise, multiple languages, etc.)
3. Begin Week 2 (account system)
4. Start Week 3 (packaging & distribution)

If issues found:
1. Document the problem
2. Debug together
3. Fix and re-test

---

**Ready to test? Let's go!** 🎙️
