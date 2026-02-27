# Prompt Templates

The app uses different prompt templates to style your voice transcriptions.

## Available Prompts

### `adhd_ramble.txt` (Default)
**Best for:** Stream-of-consciousness, rambling, scattered thoughts

**Handles:**
- Topic jumping and non-linear thinking
- Multiple ideas in one recording
- Self-corrections and backtracking
- Incomplete thoughts and verbal tics
- ADHD-style brain dumps

**Example:**
```
Input: "So um, I was thinking, like, we should probably build that expense tracker thing, oh wait, actually, maybe first we need to, uh, set up the database, no wait, I mean, we should design the UI first because that's what users see, you know?"

Output:
1. Design UI for expense tracker (user-facing priority)
2. Set up database infrastructure
3. Build expense tracker application
```

### `two_phase.txt` (Simple/Fast)
**Best for:** Clear, direct commands with minimal rambling

**Handles:**
- Basic filler word removal
- Simple commands and requests
- Technical/professional speech

**Example:**
```
Input: "Um, so basically I need you to, like, create a Python script that reads CSV files"

Output: Create a Python script that reads CSV files
```

## Switching Prompts

Edit `.env` file:
```bash
PROMPT_STYLE=adhd_ramble  # Default - handles rambling
# or
PROMPT_STYLE=two_phase    # Simpler, faster
```

## Creating Custom Prompts

1. Create `prompts/your_style.txt`
2. Use `{transcript}` placeholder for the user's speech
3. Set `PROMPT_STYLE=your_style` in `.env`
4. Restart the app

**Prompt Guidelines:**
- Extract intent first, then format second (two-phase processing)
- Remove filler words but preserve ALL meaningful content
- Don't add information that wasn't mentioned
- Match the output format to the user's intent (command vs notes vs message)
