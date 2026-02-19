"""MiniMax styling module for agentic command generation"""

import requests
from pathlib import Path
import time
import re
import json


class MinimaxStyler:
    """Styles transcripts into agentic commands using MiniMax"""
    
    def __init__(self, api_key: str, model: str = "MiniMax-M2.5", max_tokens: int = 1024, prompt_style: str = "adhd_ramble"):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.prompt_style = prompt_style
        self.base_url = "https://api.minimax.io/v1/text/chatcompletion_v2"
        
        # Load prompt template
        self.prompt_template = self._load_prompt_template()
        
    def _load_prompt_template(self) -> str:
        """Load the prompt template based on style setting"""
        prompt_path = Path(__file__).parent.parent / "prompts" / f"{self.prompt_style}.txt"
        
        if not prompt_path.exists():
            # Fallback inline prompt if file not found
            print(f"⚠️  Prompt file not found: {prompt_path}, using default")
            return self._get_default_prompt()
            
        with open(prompt_path, 'r') as f:
            return f.read()
            
    def _get_default_prompt(self) -> str:
        """Default prompt template if file not found"""
        return """<task>
You are a voice-to-text pipeline for an AI agent assistant. Your job is to clean messy speech and rewrite it as a command.

<phase1_extract_intent>
The user spoke this transcript (may include filler words, backtracking, corrections):

{transcript}

Extract the core intent:
- Remove: um, uh, like, you know, so, basically
- Remove: wait, no, actually (backtracking)
- Remove: false starts and corrections
- Keep: technical terms, proper nouns, specific requests
- Preserve: the user's actual meaning and all important details

Output the cleaned intent as concise bullet points.
</phase1_extract_intent>

<phase2_style_as_command>
Now take that cleaned intent and style it as a single concise command an engineer would write:
- Sound like a pro: technical, precise, no fluff
- Be direct: "Build X" not "I want to build X"
- Include key details but stay concise
- No markdown, no meta-commentary, just the raw command

Output ONLY the final styled command, nothing else.
</phase2_style_as_command>
</task>"""
        
    def style(self, transcript: str) -> str:
        """Convert raw transcript to styled command"""
        start_time = time.time()
        
        # Fill prompt with transcript
        prompt = self.prompt_template.format(transcript=transcript)
        
        try:
            # Make API request to MiniMax
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "max_tokens": self.max_tokens,
                "temperature": 0.7
            }
            
            response = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            response.raise_for_status()
            result = response.json()
            
            # Extract the styled command from response
            styled_command = result['choices'][0]['message']['content'].strip()
            
            latency = time.time() - start_time
            print(f"✅ MiniMax styling complete ({latency:.2f}s)")
            
            return styled_command
            
        except Exception as e:
            print(f"❌ MiniMax styling error: {e}")
            # Fallback: return cleaned transcript if API fails
            return self._basic_clean(transcript)
            
    def _basic_clean(self, text: str) -> str:
        """Basic cleaning if API fails"""
        # Remove common filler words
        fillers = ['um', 'uh', 'like', 'you know', 'so', 'basically', 'actually']
        cleaned = text
        for filler in fillers:
            cleaned = re.sub(rf'\b{filler}\b', '', cleaned, flags=re.IGNORECASE)
        
        # Remove extra whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        return cleaned


# Backwards compatibility alias
ClaudeStyler = MinimaxStyler
