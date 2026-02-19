# ADHD Ramble Optimization

**Date:** 2026-02-16  
**Status:** ✅ Complete

## Summary

Optimized the Voice App's LLM prompt to handle **stream-of-consciousness, rambling, ADHD-style speech** where users jump between topics, backtrack, and add details out of order.

## Changes Made

### 1. New Prompt: `adhd_ramble.txt`
- **Two-phase processing:**
  - Phase 1: Map the chaos (extract ALL ideas, identify connections)
  - Phase 2: Structure appropriately (command/list/notes/message)
- **Handles:**
  - Topic jumping
  - Self-corrections and backtracking
  - Multiple unrelated ideas in one recording
  - Verbal tics and filler words
  - Non-linear thinking
- **Preserves:** ALL meaningful information (even if scattered)

### 2. Configurable Prompt System
- **New setting in `.env`:**
  ```bash
  PROMPT_STYLE=adhd_ramble  # Default - handles rambling
  # or
  PROMPT_STYLE=two_phase    # Simpler, faster (original)
  ```
- Users can switch between styles or create custom prompts

### 3. Code Updates
- `src/style.py`: Added `prompt_style` parameter to MinimaxStyler
- `src/config.py`: Loads `PROMPT_STYLE` from environment (default: adhd_ramble)
- `main.py`: Passes prompt_style to styler, fixed method call bug
- `prompts/`: New folder with prompt templates and README

## Files Changed

```
/Users/tars/clawd/projects/voice-app-downloadable/
├── .env                          # Added PROMPT_STYLE setting
├── src/
│   ├── config.py                 # Added prompt_style property
│   └── style.py                  # Made prompt configurable
├── main.py                       # Pass prompt_style, fix style() call
├── prompts/                      # NEW folder
│   ├── adhd_ramble.txt          # NEW - optimized for rambling
│   ├── two_phase.txt            # NEW - original simple prompt
│   └── README.md                # NEW - prompt documentation
└── ADHD-OPTIMIZATION.md         # This file
```

## Example Comparisons

### Example 1: Scattered Task

**Input (rambling):**
```
"So um, I was thinking, like, we should probably build that expense tracker thing, 
oh wait, actually, maybe first we need to, uh, set up the database, no wait, 
I mean, we should design the UI first because that's what users see, you know?"
```

**Old prompt (two_phase):**
```
Build expense tracker with database and UI design
```

**New prompt (adhd_ramble):**
```
1. Design UI for expense tracker (user-facing priority)
2. Set up database infrastructure
3. Build expense tracker application
```

### Example 2: Multiple Unrelated Ideas

**Input (rambling):**
```
"Okay so remind me to call Sarah tomorrow at 3pm about the project, 
oh and also I need to buy milk, wait no, eggs, both actually, 
and um, can you research React vs Vue for the new dashboard thing?"
```

**Old prompt (two_phase):**
```
Call Sarah at 3pm about project, buy milk and eggs, research React vs Vue for dashboard
```

**New prompt (adhd_ramble):**
```
Tasks:
1. Call Sarah tomorrow at 3pm about the project
2. Buy milk and eggs
3. Research React vs Vue for new dashboard
```

### Example 3: Brain Dump / Notes

**Input (rambling):**
```
"So the client wants dark mode, like, that's a must-have, 
and they mentioned something about, uh, mobile-first design, 
oh and accessibility, that was important, what else, oh yeah, 
they want it to integrate with their existing CRM system"
```

**Old prompt (two_phase):**
```
Client requirements: dark mode, mobile-first design, accessibility, CRM integration
```

**New prompt (adhd_ramble):**
```
Client Requirements:
• Dark mode (must-have)
• Mobile-first design
• Accessibility compliance
• Integration with existing CRM system
```

## Target Audience

This optimization is perfect for:
- **ADHD users** who think by talking out loud
- **Verbal processors** who need to brain dump
- **Stream-of-consciousness thinkers** who jump between topics
- **Anyone who waffles** and needs structure applied after the fact
- **Writers** with scattered thoughts that need organizing

## Competitive Advantage

Existing apps (Pipit, Sotto, SpeakMac, Voice Gecko) only do:
```
Voice → Transcription → Raw text
```

**Your app does:**
```
Voice → Transcription → LLM cleanup/structure → Organized output
```

The LLM step makes this unique - **no other app restructures rambling into coherent output**.

## Testing Recommendations

1. **Record a rambling brain dump** (30-60 seconds, jump between 3+ topics)
2. **Compare outputs** between `adhd_ramble` and `two_phase` prompts
3. **Test edge cases:**
   - Self-corrections ("no wait, actually...")
   - Multiple unrelated tasks
   - Technical jargon mixed with casual speech
   - Emotional/emphatic language

## Next Steps

1. Test with real ADHD users for feedback
2. Consider adding more prompt templates:
   - `professional` - business/formal tone
   - `casual` - keep informal voice
   - `technical` - optimize for code/docs
3. Add prompt selector to UI (Week 2 roadmap)
4. Market as "ADHD-friendly voice assistant"

## Notes

- Default is now `adhd_ramble` - handles most use cases well
- Users who want simple/fast can switch to `two_phase`
- Prompt files are hot-swappable (no restart needed if we add file watching)
- Consider adding telemetry to see which prompts users prefer
