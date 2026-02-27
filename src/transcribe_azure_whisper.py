"""Azure OpenAI Whisper transcription module"""

from openai import AzureOpenAI
from typing import Optional
import time
import tempfile
import os


class AzureWhisperTranscriber:
    """Transcribes audio using Azure OpenAI Whisper"""
    
    def __init__(self, api_key: str, endpoint: str, deployment: str = "whisper"):
        self.api_key = api_key
        self.endpoint = endpoint
        self.deployment = deployment
        self.client = AzureOpenAI(
            api_key=api_key,
            api_version="2024-02-01",
            azure_endpoint=endpoint
        )
        
    def transcribe_sync(self, audio_bytes: bytes) -> str:
        """
        Transcribe audio bytes to text using Azure OpenAI Whisper
        
        Args:
            audio_bytes: WAV format audio bytes
            
        Returns:
            Transcribed text
        """
        start_time = time.time()
        
        try:
            print(f"📡 Sending to Azure OpenAI Whisper...")
            
            # Whisper API requires a file, not bytes
            # Create a temporary file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                temp_file.write(audio_bytes)
                temp_path = temp_file.name
            
            try:
                # Make API call
                with open(temp_path, 'rb') as audio_file:
                    response = self.client.audio.transcriptions.create(
                        model=self.deployment,
                        file=audio_file,
                        response_format="text"
                    )
                
                # Response is just the text string
                transcript = response.strip()
                
                latency = (time.time() - start_time) * 1000
                print(f"✅ Azure Whisper transcription complete ({latency:.0f}ms)")
                print(f"📝 Transcript: {transcript[:100]}..." if len(transcript) > 100 else f"📝 Transcript: {transcript}")
                
                return transcript
                
            finally:
                # Clean up temp file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            
        except Exception as e:
            print(f"❌ Azure Whisper error: {e}")
            import traceback
            traceback.print_exc()
            raise
