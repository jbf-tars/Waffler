"""Configuration loader for the pipeline"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv

# Load .env files — user data dir first, then project root as fallback

_user_env = Path.home() / ".waffler-hosted" / ".env"
if _user_env.exists():
    load_dotenv(str(_user_env), override=True)
load_dotenv(override=True)  # Also check project root .env as fallback


class Config:
    """Load and manage configuration from YAML and environment variables"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._load_env_vars()
        
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
            
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)
            
    def _load_env_vars(self):
        """Load API keys from environment variables"""
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        self.groq_api_key = os.getenv('GROQ_API_KEY')
        self.prompt_style = os.getenv('PROMPT_STYLE', 'normal')

        # Flag whether we have enough config to run the pipeline
        # Groq alone is sufficient (handles both transcription + styling)
        self.has_api_key = bool(self.groq_api_key or self.openai_api_key)
        if not self.has_api_key:
            print("No API key found — setup wizard will be shown")

    def reload_env(self):
        """Re-read .env and refresh keys. Called after wizard sets a key."""
        user_env = Path.home() / ".waffler-hosted" / ".env"
        if user_env.exists():
            load_dotenv(str(user_env), override=True)
        load_dotenv(override=True)
        self._load_env_vars()
            
    def get(self, key_path: str, default=None) -> Any:
        """Get config value using dot notation (e.g., 'audio.sample_rate')"""
        keys = key_path.split('.')
        value = self.config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
                
        return value
        
    @property
    def hotkey(self) -> str:
        return self.get('hotkey.key', 'f13')
        
    @property
    def sample_rate(self) -> int:
        return self.get('audio.sample_rate', 16000)
        
    @property
    def channels(self) -> int:
        return self.get('audio.channels', 1)
        
    @property
    def notifications_enabled(self) -> bool:
        return self.get('notifications.enabled', True)
