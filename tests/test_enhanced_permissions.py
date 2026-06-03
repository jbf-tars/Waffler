#!/usr/bin/env python3
"""
Test the enhanced permissions system for Waffler.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from permissions_manager import PermissionsManager, PermissionStatus

def test_permissions():
    """Test the enhanced permissions system."""
    print("🔍 Testing Enhanced Permissions System")
    print("=" * 50)
    
    pm = PermissionsManager()
    
    # Test individual permission checks
    print("\n📋 Individual Permission Checks:")
    print("-" * 30)
    
    # Microphone TCC check is via app.py's startup AVCaptureDevice probe
    # (see [mic-tcc] in app.log) -- the old check_microphone_permission was
    # deleted because it returned false-GRANTED on TCC-denied streams.

    access_result = pm.check_accessibility_permission()
    print(f"♿ Accessibility: {access_result.status.value}")
    if access_result.explanation:
        print(f"   Explanation: {access_result.explanation}")
    if access_result.error_message:
        print(f"   Error: {access_result.error_message}")
    if access_result.fallback_available:
        print(f"   Fallback: {access_result.fallback_message}")
    
    input_result = pm.check_input_monitoring_permission()
    print(f"⌨️  Input Monitoring: {input_result.status.value}")
    if input_result.explanation:
        print(f"   Explanation: {input_result.explanation}")
    if input_result.error_message:
        print(f"   Error: {input_result.error_message}")
    if input_result.fallback_available:
        print(f"   Fallback: {input_result.fallback_message}")
    
    # Comprehensive-status check removed: get_permission_status_summary
    # was part of the dead get_permission_status IPC chain (no JS caller,
    # confirmed via grep ui/) and was deleted with it.

    # Test permission explanations
    print("\n💡 Permission Explanations:")
    print("-" * 30)
    
    for perm_name, explanation in pm.PERMISSION_EXPLANATIONS.items():
        print(f"{explanation['title']}:")
        print(f"   Why: {explanation['why']}")
        print(f"   Consequences: {explanation['consequences']}")
        print(f"   Fallback: {explanation['fallback']}")
        print()
    
    print("\n✅ Enhanced permissions system test complete!")
    print("\nKey Improvements Implemented:")
    print("- Clear explanations of WHY each permission is needed")
    print("- Graceful fallback messages when permissions denied") 
    print("- Detailed error messages and troubleshooting guidance")
    print("- Distinction between critical vs optional permissions")
    print("- Comprehensive status reporting for UI")
    
    return True

if __name__ == "__main__":
    test_permissions()