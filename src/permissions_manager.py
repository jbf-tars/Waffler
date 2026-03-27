#!/usr/bin/env python3
"""
Enhanced macOS Permissions Manager for Waffler
Provides improved UX for accessibility, microphone, and input monitoring permissions.
"""

import platform
import subprocess as sp
from typing import Dict, Optional, Tuple
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

    def check_microphone_permission(self) -> PermissionResult:
        """Check microphone access with detailed feedback."""
        try:
            import sounddevice as sd
            # Try to create a stream to test mic access
            stream = sd.InputStream(samplerate=16000, channels=1, dtype='int16', blocksize=1024)
            stream.start()
            stream.stop()
            stream.close()
            
            return PermissionResult(
                status=PermissionStatus.GRANTED,
                explanation="Microphone access is working properly"
            )
            
        except Exception as e:
            error_msg = str(e).lower()
            
            if "permission" in error_msg or "access" in error_msg:
                return PermissionResult(
                    status=PermissionStatus.DENIED,
                    error_message="Microphone access was denied",
                    explanation="Click 'Allow' when prompted, or open System Settings to grant permission",
                    fallback_available=True,
                    fallback_message="You can use keyboard-only mode without voice features"
                )
            else:
                return PermissionResult(
                    status=PermissionStatus.UNKNOWN,
                    error_message=f"Microphone test failed: {str(e)}",
                    explanation="There may be a hardware or configuration issue with your microphone"
                )

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

    def check_all_permissions(self) -> Dict[str, PermissionResult]:
        """Check all required permissions and return comprehensive status."""
        return {
            "microphone": self.check_microphone_permission(),
            "accessibility": self.check_accessibility_permission(),
            "input_monitoring": self.check_input_monitoring_permission()
        }

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

    def get_permission_status_summary(self) -> Dict[str, any]:
        """Get a summary of all permission statuses for UI display."""
        results = self.check_all_permissions()
        
        summary = {
            "all_granted": True,
            "permissions": {},
            "missing_critical": [],
            "missing_optional": [],
            "recommendations": []
        }
        
        for perm_name, result in results.items():
            if result.status == PermissionStatus.NOT_APPLICABLE:
                continue
                
            is_granted = result.status == PermissionStatus.GRANTED
            is_critical = perm_name in ["microphone"]  # Microphone is critical
            
            summary["permissions"][perm_name] = {
                "granted": is_granted,
                "critical": is_critical,
                "title": self.PERMISSION_EXPLANATIONS[perm_name]["title"],
                "explanation": result.explanation,
                "error": result.error_message,
                "fallback_available": result.fallback_available,
                "fallback_message": result.fallback_message
            }
            
            if not is_granted:
                summary["all_granted"] = False
                if is_critical:
                    summary["missing_critical"].append(perm_name)
                else:
                    summary["missing_optional"].append(perm_name)
        
        # Generate recommendations
        if summary["missing_critical"]:
            summary["recommendations"].append("Critical permissions missing - some features won't work")
        if summary["missing_optional"]:
            summary["recommendations"].append("Optional permissions missing - some convenience features disabled")
        if not summary["missing_critical"] and not summary["missing_optional"]:
            summary["recommendations"].append("All permissions granted - full functionality available")
            
        return summary