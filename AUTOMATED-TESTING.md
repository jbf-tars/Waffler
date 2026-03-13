# Automated Testing

**Problem:** Testing the voice app manually is tedious - you have to:
1. Run the app
2. Press the hotkey
3. Speak into the microphone
4. Check if it worked

**Solution:** Automated testing with pre-recorded audio files.

---

## Quick Start

### Option 1: Record a Test Audio File (Recommended)

```bash
# Record 5 seconds of test audio
python3 record_test_audio.py

# Say: "Hello, this is a test of the Waffler application"
# It will save to test_audio/sample.wav

# Run the automated test
python3 test_with_audio.py
```

### Option 2: Use Your Own Audio File

```bash
# Test with any WAV file (16kHz, mono recommended)
python3 test_with_audio.py path/to/your/audio.wav
```

---

## What It Tests

The automated test runs the full pipeline:

1. **Transcription** (Deepgram STT)
   - Reads audio file
   - Sends to Deepgram
   - Verifies transcript is generated
   - Reports latency

2. **Styling** (MiniMax LLM)
   - Takes transcript
   - Sends to MiniMax
   - Verifies command is generated
   - Reports latency

3. **Clipboard** (System integration)
   - Copies command to clipboard
   - Pastes and verifies
   - Confirms clipboard integration works

4. **Total Latency**
   - Reports combined time
   - Checks if under target (<3s)

---

## Example Output

```
🧪 Waffler Automated Test
============================================================

📁 Reading audio file: test_audio/sample.wav
📊 Audio size: 131244 bytes

📡 Testing Deepgram transcription...
✓ Transcript (1847ms): Hello, this is a test

🤖 Testing MiniMax styling...
✓ Command (2156ms): Test the Waffler application

📋 Testing clipboard...
✓ Clipboard: Test the Waffler application

============================================================
✅ ALL TESTS PASSED (4003ms total)
   • Transcribe: 1847ms
   • Style: 2156ms
============================================================
```

---

## Benefits

**No more manual testing:**
- Run tests whenever you make changes
- Test without holding down hotkeys
- Test without speaking into microphone
- Consistent test cases (same audio every time)

**Faster iteration:**
- Change code → run test → see results in seconds
- No need to grant accessibility permission for tests
- Can run tests on different machines without microphone setup

**Automated CI/CD:**
- Can integrate into GitHub Actions
- Run tests before deploying new versions
- Catch bugs before they reach users

---

## Creating Test Audio Files

### Method 1: Record via script (easiest)

```bash
python3 record_test_audio.py 5  # Record 5 seconds
```

### Method 2: Use TTS (if you have tts tool)

```bash
# Generate audio with text-to-speech
tts "Hello, this is a test" --output test_audio/sample.wav
```

### Method 3: Record via system

```bash
# macOS: Use QuickTime Player
# Record Audio → Save as test_audio/sample.wav
# Convert to 16kHz mono if needed:
# ffmpeg -i input.wav -ar 16000 -ac 1 test_audio/sample.wav
```

---

## Multiple Test Cases

Create different test files for different scenarios:

```bash
test_audio/
├── hello.wav          # Simple phrase
├── ramble.wav         # ADHD-style rambling speech
├── command.wav        # Direct command
├── technical.wav      # Technical jargon
└── noisy.wav          # Background noise test
```

Test them all:

```bash
for audio in test_audio/*.wav; do
    echo "Testing $audio..."
    python3 test_with_audio.py "$audio"
done
```

---

## Next Steps

1. **Record test audio** once: `python3 record_test_audio.py`
2. **Run tests** whenever you change code: `python3 test_with_audio.py`
3. **Add more test cases** as needed
4. **Automate** - run tests before every commit

---

## Troubleshooting

**"No test audio file found"**
- Run `python3 record_test_audio.py` first
- Or provide a file: `python3 test_with_audio.py your_file.wav`

**"Transcript is empty"**
- Audio file might be silent
- Check audio file plays sound: `afplay test_audio/sample.wav`
- Re-record with louder voice

**"Deepgram API error"**
- Check `.env` has valid `DEEPGRAM_API_KEY`
- Check internet connection
- Check Deepgram account has credits

**"MiniMax API error"**
- Check `.env` has valid `MINIMAX_API_KEY`
- Check MiniMax account has credits
- Try reducing `max_tokens` in config.yaml
