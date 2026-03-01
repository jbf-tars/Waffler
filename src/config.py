"""Configuration loader for the pipeline"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv

# Load .env files — user data dir first, then project root as fallback
# Nuke any old shell-level keys that might interfere
for _var in ['AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT', 'MINIMAX_API_KEY', 'DEEPGRAM_API_KEY']:
    os.environ.pop(_var, None)

_user_env = Path.home() / ".waffler" / ".env"
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
        self.deepgram_api_key = os.getenv('DEEPGRAM_API_KEY')
        self.minimax_api_key = os.getenv('MINIMAX_API_KEY')
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        self.groq_api_key = os.getenv('GROQ_API_KEY')
        self.azure_openai_api_key = os.getenv('AZURE_OPENAI_API_KEY')
        self.azure_openai_endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
        self.prompt_style = os.getenv('PROMPT_STYLE', 'normal')

        # Backend (hosted production mode)
        self.backend_url = os.getenv('BACKEND_URL', '')
        self.app_secret = os.getenv('APP_SECRET', '')

        # Flag whether we have enough config to run the pipeline
        # Backend URL alone is sufficient (keys live on the server)
        # Groq alone is also sufficient (handles both transcription + styling)
        self.has_api_key = bool(
            self.backend_url or
            self.groq_api_key or self.openai_api_key or
            self.deepgram_api_key or self.azure_openai_api_key
        )
        if not self.has_api_key:
            print("No API key found — setup wizard will be shown")

    def reload_env(self):
        """Re-read .env and refresh keys. Called after wizard sets a key."""
        user_env = Path.home() / ".waffler" / ".env"
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
    def deepgram_model(self) -> str:
        return self.get('deepgram.model', 'nova-2')
        
    @property
    def deepgram_language(self) -> str:
        return self.get('deepgram.language', 'en-US')
        
    @property
    def minimax_model(self) -> str:
        return self.get('minimax.model', 'MiniMax-M2.5')
        
    @property
    def minimax_max_tokens(self) -> int:
        return self.get('minimax.max_tokens', 1024)
        
    @property
    def notifications_enabled(self) -> bool:
        return self.get('notifications.enabled', True)
