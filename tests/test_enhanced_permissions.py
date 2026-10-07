#!/usr/bin/env python3
"""
Test the enhanced permissions system for Waffler.

This used to print each result and return True, so it could never fail
(pytest warned "returned <class 'bool'>"). It now asserts what it printed:
each check gives a real status, a denied permission says why, and every
explanation has its four parts.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from permissions_manager import PermissionResult, PermissionsManager, PermissionStatus


def _check(result):
    assert isinstance(result, PermissionResult)
    assert isinstance(result.status, PermissionStatus)
    if result.status is PermissionStatus.DENIED:
        assert result.error_message or result.explanation, "a denied permission must say why"
    if result.fallback_available:
        assert result.fallback_message, "a fallback that is offered must be described"
    if sys.platform != "darwin":
        # Accessibility and Input Monitoring are macOS permissions.
        assert result.status is PermissionStatus.NOT_APPLICABLE
        assert result.explanation


def test_permissions():
    """Test the enhanced permissions system."""
    pm = PermissionsManager()

    # Microphone TCC check is via app.py's startup AVCaptureDevice probe
    # (see [mic-tcc] in app.log) -- the old check_microphone_permission was
    # deleted because it returned false-GRANTED on TCC-denied streams.
    _check(pm.check_accessibility_permission())
    _check(pm.check_input_monitoring_permission())

    # Comprehensive-status check removed: get_permission_status_summary
    # was part of the dead get_permission_status IPC chain (no JS caller,
    # confirmed via grep ui/) and was deleted with it.

    # Every permission has a plain explanation: why it is needed, what
    # happens without it, and the fallback.
    assert {"microphone", "accessibility", "input_monitoring"} <= set(pm.PERMISSION_EXPLANATIONS)
    for perm_name, explanation in pm.PERMISSION_EXPLANATIONS.items():
        for part in ("title", "why", "consequences", "fallback"):
            assert isinstance(explanation.get(part), str) and explanation[part].strip(), (
                perm_name, part)


if __name__ == "__main__":
    test_permissions()
    print("permissions checks passed")
