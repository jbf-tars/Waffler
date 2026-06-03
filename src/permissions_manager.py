#!/usr/bin/env python3
"""
Enhanced macOS Permissions Manager for Waffler
Provides improved UX for accessibility, microphone, and input monitoring permissions.
"""

import platform
import subprocess as sp
from typing import Dict, Optional
from dataclasses import dataclass
from enum import Enum

class PermissionStatus(Enum):
    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"

@dataclass
class PermissionResult:
    status: PermissionStatus
    error_message: Optional[str] = None
    explanation: Optional[str] = None
    fallback_available: bool = False
    fallback_message: Optional[str] = None

class PermissionsManager:
    """Enhanced permissions manager with improved UX and error handling."""
    
    def __init__(self):
        self.platform = platform.system()
        
    # Permission explanations - WHY each permission is needed
    PERMISSION_EXPLANATIONS = {
        "microphone": {
            "title": "Microphone Access",
            "why": "To record your voice for transcription to text",
            "consequences": "Without this, Waffler cannot hear your voice commands or dictation",
            "fallback": "You can use the keyboard-only mode, but voice features will be disabled"
        },
        "accessibility": {
            "title": "Accessibility",
            "why": "To automatically paste transcribed text into other applications",
            "consequences": "Without this, you'll need to manually copy/paste text from Waffler",
            "fallback": "Text will appear in the Waffler window for manual copying"
        },
        "input_monitoring": {
            "title": "Input Monitoring",
            "why": "To detect Fn key presses for voice activation",
            "consequences": "Without this, Fn key detection won't work",
            "fallback": "Use F13 key or alternative hotkey combinations instead"
        }
    }

    def check_accessibility_permission(self) -> PermissionResult:
        """Check accessibility permission with enhanced feedback."""
        if self.platform != "Darwin":
            return PermissionResult(
                status=PermissionStatus.NOT_APPLICABLE,
                explanation="Accessibility permission not needed on this platform"
            )
        
        try:
            from ApplicationServices import AXIsProcessTrusted
            is_trusted = AXIsProcessTrusted()
            
            if is_trusted:
                return PermissionResult(
                    status=PermissionStatus.GRANTED,
                    explanation="Accessibility permission granted - automatic text pasting enabled"
                )
            else:
                return PermissionResult(
                    status=PermissionStatus.DENIED,
                    error_message="Accessibility permission not granted",
                    explanation="Grant this permission to enable automatic text pasting into other apps",
                    fallback_available=True,
                    fallback_message="Text will appear in Waffler for manual copying"
                )
                
        except ImportError:
            return PermissionResult(
                status=PermissionStatus.UNKNOWN,
                error_message="Cannot check accessibility permission (PyObjC not available)",
                explanation="Please grant permission manually in System Settings > Privacy & Security > Accessibility"
            )
        except Exception as e:
            return PermissionResult(
                status=PermissionStatus.UNKNOWN,
                error_message=f"Error checking accessibility permission: {str(e)}",
                explanation="There was an issue checking the permission status"
            )

    def check_input_monitoring_permission(self) -> PermissionResult:
        """Check input monitoring permission for Fn key detection."""
        if self.platform != "Darwin":
            return PermissionResult(
                status=PermissionStatus.NOT_APPLICABLE,
                explanation="Input monitoring not needed on this platform"
            )
        
        try:
            from Quartz import (
                CGEventTapCreate, kCGSessionEventTap, kCGHeadInsertEventTap,
                kCGEventTapOptionDefault, CGEventMaskBit, kCGEventKeyDown,
                CFRelease
            )
            
            # Try to create a temporary event tap to test permission
            event_mask = CGEventMaskBit(kCGEventKeyDown)
            event_tap = CGEventTapCreate(
                kCGSessionEventTap,
                kCGHeadInsertEventTap,
                kCGEventTapOptionDefault,
                event_mask,
                lambda *args: None,
                None
            )
            
            if event_tap is not None:
                # Permission granted - clean up
                CFRelease(event_tap)
                return PermissionResult(
                    status=PermissionStatus.GRANTED,
                    explanation="Input monitoring enabled - Fn key detection available"
                )
            else:
                return PermissionResult(
                    status=PermissionStatus.DENIED,
                    error_message="Input monitoring permission not granted",
                    explanation="Grant this permission to enable Fn key detection for voice activation",
                    fallback_available=True,
                    fallback_message="Use F13 key or other hotkey combinations instead"
                )
                
        except Exception as e:
            return PermissionResult(
                status=PermissionStatus.UNKNOWN,
                error_message=f"Error checking input monitoring permission: {str(e)}",
                explanation="Could not verify input monitoring status"
            )

    def request_microphone_permission(self) -> Dict[str, any]:
        """Enhanced microphone permission request with better UX."""
        if self.platform == "Darwin":
            try:
                import sounddevice as sd
                # Create a brief recording to trigger permission dialog
                stream = sd.InputStream(samplerate=16000, channels=1, dtype='int16')
                stream.start()
                import time
                time.sleep(0.1)
                stream.stop()
                stream.close()
                return {
                    "ok": True, 
                    "message": "Permission dialog should have appeared. Please allow microphone access."
                }
            except Exception as e:
                return {
                    "ok": False, 
                    "error": str(e),
                    "fallback": "Open System Settings > Privacy & Security > Microphone and add Waffler"
                }
        else:
            return self.open_permission_settings("microphone")

    def request_accessibility_permission(self) -> Dict[str, any]:
        """Enhanced accessibility permission request with step-by-step guidance."""
        if self.platform != "Darwin":
            return {"ok": True, "message": "Not needed on this platform"}

        try:
            # Attempt to trigger permission dialog by using accessibility features
            from ApplicationServices import AXIsProcessTrusted
            if AXIsProcessTrusted():
                return {"ok": True, "message": "Accessibility already granted"}

            # Try to trigger the permission dialog
            try:
                from Quartz import (
                    CGWindowListCopyWindowInfo,
                    kCGWindowListOptionOnScreenOnly,
                    kCGNullWindowID
                )
                # This should trigger the permission dialog
                _ = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
            except Exception:
                pass

            return {
                "ok": True, 
                "message": "Please grant accessibility permission in System Settings",
                "steps": [
                    "A dialog may have appeared - click 'Open System Preferences'",
                    "Or manually open System Settings > Privacy & Security > Accessibility", 
                    "Find Waffler in the list and toggle it ON",
                    "If Waffler isn't listed, click + and add Waffler.app",
                    "Return to Waffler and click 'Recheck'"
                ]
            }

        except Exception as e:
            return {
                "ok": False, 
                "error": str(e),
                "fallback": "Manually open System Settings > Privacy & Security > Accessibility"
            }

    def open_permission_settings(self, permission_type: str) -> Dict[str, any]:
        """Open the specific system settings page for a permission."""
        try:
            if self.platform == "Windows":
                if permission_type == "microphone":
                    sp.Popen(["start", "ms-settings:privacy-microphone"], shell=True)
                    return {"ok": True, "message": "Opened Windows microphone settings"}
                    
            elif self.platform == "Darwin":
                urls = {
                    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
                    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility", 
                    "input_monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
                }
                
                if permission_type in urls:
                    sp.Popen(["open", urls[permission_type]])
                    return {
                        "ok": True, 
                        "message": f"Opened System Settings for {permission_type}",
                        "next_steps": f"Toggle Waffler ON in the {permission_type} section"
                    }
                    
            return {"ok": False, "error": f"Unknown permission type: {permission_type}"}
            
        except Exception as e:
            return {"ok": False, "error": str(e)}

