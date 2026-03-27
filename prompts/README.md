# Prompt Templates

Waffler uses prompt templates to style voice transcriptions via the LLM.

## Available Prompts

### `normal.txt` (Default)
**Best for:** General-purpose voice-to-text cleanup

**Handles:**
- Filler word removal (um, uh, like, you know)
- Sentence structure cleanup
- Preserving intent and meaning
- Natural, clear output

## Switching Prompts

Set the `PROMPT_STYLE` in your `.env` file:
```bash
PROMPT_STYLE=normal
```

## Creating Custom Prompts

1. Create `prompts/your_style.txt`
2. Use `{transcript}` placeholder for the user's speech
3. Set `PROMPT_STYLE=your_style` in `.env`
4. Restart the app

**Guidelines:**
- Remove filler words but preserve all meaningful content
- Don't add information that wasn't spoken
- Match output format to the user's intent (command vs notes vs message)
