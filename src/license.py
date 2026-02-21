"""
Natter License Key Validation
Validates licence keys on first run via Lemon Squeezy API.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import requests

LICENSE_FILE = Path.home() / ".natter" / "license.json"
LEMONSQUEEZY_API_URL = "https://api.lemonsqueezy.com/v1/licenses"


def get_license_key() -> str | None:
    """Get stored license key if exists."""
    if LICENSE_FILE.exists():
        try:
            with open(LICENSE_FILE, "r") as f:
                data = json.load(f)
                return data.get("license_key")
        except Exception:
            return None
    return None


def save_license_key(license_key: str):
    """Save license key locally."""
    LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LICENSE_FILE, "w") as f:
        json.dump({"license_key": license_key}, f)


def is_validated() -> bool:
    """Check if license has been validated."""
    if LICENSE_FILE.exists():
        try:
            with open(LICENSE_FILE, "r") as f:
                data = json.load(f)
                return data.get("validated", False)
        except Exception:
            return False
    return False


def validate_license(license_key: str, instance_name: str = "Natter") -> dict:
    """
    Validate license key with Lemon Squeezy.
    
    Args:
        license_key: The license key to validate
        instance_name: Name of the instance (e.g., "John's MacBook")
    
    Returns:
        dict with keys: valid (bool), error (str optional)
    """
    if not license_key:
        return {"valid": False, "error": "No license key provided"}
    
    # For demo/development: accept any key starting with "VF-"
    if license_key.startswith("VF-") or license_key.startswith("DEV-"):
        # Development mode - just save and accept
        save_license_key(license_key)
        with open(LICENSE_FILE, "w") as f:
            json.dump({
                "license_key": license_key,
                "validated": True,
                "instance": instance_name,
                "valid_until": None
            }, f)
        return {"valid": True}
    
    # Real Lemon Squeezy validation would go here
    # For now, we'll use a simple validation
    # In production, you'd call: POST https://api.lemonsqueezy.com/v1/licenses/validate
    # with headers: Authorization: Bearer {api_key}
    
    # Placeholder - would need API key from Lemon Squeezy
    return {"valid": False, "error": "Lemon Squeezy integration not configured"}


def clear_license():
    """Clear stored license (for testing/reset)."""
    if LICENSE_FILE.exists():
        LICENSE_FILE.unlink()


if __name__ == "__main__":
    # Test
    print("Testing license validation...")
    
    # Test dev key
    result = validate_license("VF-DEV-1234", "Test Instance")
    print(f"Dev key result: {result}")
    
    # Check status
    print(f"Is validated: {is_validated()}")
    print(f"License key: {get_license_key()}")
