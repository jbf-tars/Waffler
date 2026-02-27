"""macOS notification support"""

import subprocess
import platform


class NotificationManager:
    """Send system notifications"""
    
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.is_macos = platform.system() == 'Darwin'
        
    def notify(self, title: str, message: str, sound: bool = False):
        """
        Send a system notification
        
        Args:
            title: Notification title
            message: Notification message
            sound: Play sound with notification
        """
        if not self.enabled:
            return
            
        if self.is_macos:
            self._notify_macos(title, message, sound)
        else:
            # Fallback: just print
            print(f"🔔 {title}: {message}")
            
    def _notify_macos(self, title: str, message: str, sound: bool):
        """Send notification using macOS osascript"""
        try:
            script = f'display notification "{message}" with title "{title}"'
            if sound:
                script += ' sound name "Glass"'
                
            subprocess.run(['osascript', '-e', script], 
                          check=False, 
                          capture_output=True)
        except Exception as e:
            print(f"⚠️  Notification failed: {e}")
            
    def listening(self):
        """Show 'listening' notification"""
        self.notify("Voice Pipeline", "🎤 Listening...", sound=False)
        
    def processing(self):
        """Show 'processing' notification"""
        self.notify("Voice Pipeline", "⚙️ Processing...", sound=False)
        
    def ready(self, preview: str = ""):
        """Show 'ready' notification with command preview"""
        message = "✅ Ready! Press Cmd+V to paste"
        if preview:
            # Truncate preview to 50 chars
            preview_short = preview[:50] + "..." if len(preview) > 50 else preview
            message = f"✅ {preview_short}"
            
        self.notify("Voice Pipeline", message, sound=True)
        
    def error(self, error_msg: str):
        """Show error notification"""
        self.notify("Voice Pipeline", f"❌ Error: {error_msg}", sound=True)
