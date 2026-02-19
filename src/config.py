"""Configuration loader for the pipeline"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv

# Load .env file from project root
# Nuke any old shell-level keys that might interfere — only .env should matter
for _var in ['AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT', 'MINIMAX_API_KEY', 'DEEPGRAM_API_KEY']:
    os.environ.pop(_var, None)

load_dotenv(override=True)  # Always prefer .env over existing shell env vars


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
        self.azure_openai_api_key = os.getenv('AZURE_OPENAI_API_KEY')
        self.azure_openai_endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
        self.prompt_style = os.getenv('PROMPT_STYLE', 'smart')
        
        # Require either Deepgram, OpenAI, or Azure OpenAI (Whisper)
        if not self.deepgram_api_key and not self.openai_api_key and not self.azure_openai_api_key:
            raise ValueError("Either DEEPGRAM_API_KEY, OPENAI_API_KEY, or AZURE_OPENAI_API_KEY must be set")
        # MiniMax is optional - OpenAI GPT will be used if no MiniMax key
            
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
