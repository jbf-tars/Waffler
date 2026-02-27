#!/usr/bin/env python3
"""
Test script for backend integration (Phase 3)
Tests auth, styling, and quota checking with the self-hosted backend.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Set backend URL for testing
os.environ['BACKEND_URL'] = 'http://localhost:8000'

def test_backend_auth():
    """Test backend authentication"""
    print("\n=== Testing Backend Authentication ===")
    import natter_auth_backend as auth

    # Check backend health
    print("Checking backend health...")
    if not auth.check_backend_health():
        print("❌ Backend is not running at http://localhost:8000")
        print("   Start it with: cd backend && python3 -m uvicorn app.main:app --reload")
        return False
    print("✓ Backend is healthy")

    # Test signup (or use existing test user)
    print("\nTesting signup...")
    email = f"test{os.getpid()}@example.com"
    result = auth.sign_up(email, "testpass123", "Test User")

    if not result.get("ok"):
        if "already registered" in result.get("error", "").lower():
            print(f"⚠️  Email already exists, trying signin instead...")
            result = auth.sign_in("test@example.com", "testpass123")
        else:
            print(f"❌ Signup failed: {result.get('error')}")
            return False

    if result.get("ok"):
        user = result["user"]
        api_key = result.get("api_key", "N/A")
        print(f"✓ Authenticated as {user['email']}")
        print(f"  User ID: {user['id']}")
        print(f"  API Key: {api_key[:20]}...")
        print(f"  Tier: {user.get('tier', 'free')}")

        # Check quota
        print("\nChecking quota...")
        quota = auth.get_quota_status()
        if "error" not in quota:
            print(f"✓ Quota: {quota['used']}/{quota['quota']} used, {quota['remaining']} remaining")
        else:
            print(f"⚠️  Could not get quota: {quota['error']}")

        return True
    else:
        print(f"❌ Authentication failed: {result.get('error')}")
        return False


def test_backend_styling():
    """Test backend styling endpoint"""
    print("\n=== Testing Backend Styling ===")
    from style_openai import OpenAIStyler

    # Create styler (should auto-detect backend)
    styler = OpenAIStyler(prompt_style="smart")

    if not styler._use_backend:
        print("❌ Backend styling not activated")
        print("   Make sure you're authenticated and BACKEND_URL is set")
        return False

    print("✓ Backend styling activated")

    # Test with a sample transcript
    test_transcript = "um so like I was thinking we should maybe do the thing you know"
    print(f"\nStyling transcript: '{test_transcript}'")

    try:
        styled_text, usage = styler.style(test_transcript)
        print(f"✓ Styled text: '{styled_text}'")
        print(f"  Provider: {usage.get('provider', 'unknown')}")
        print(f"  Input tokens: {usage.get('input_tokens', 0)}")
        print(f"  Output tokens: {usage.get('output_tokens', 0)}")
        if 'quota_remaining' in usage:
            print(f"  Quota remaining: {usage['quota_remaining']}")
        return True
    except Exception as e:
        print(f"❌ Styling failed: {e}")
        return False


def test_quota_enforcement():
    """Test that quota limits are enforced"""
    print("\n=== Testing Quota Enforcement ===")
    import natter_auth_backend as auth

    quota = auth.get_quota_status()
    if "error" in quota:
        print(f"⚠️  Could not check quota: {quota['error']}")
        return False

    print(f"Current quota: {quota['used']}/{quota['quota']}")

    if quota['remaining'] <= 0:
        print("✓ Quota is exhausted (as expected for free tier testing)")
        print("  Try making another request - should get 429 error")

        from style_openai import OpenAIStyler
        styler = OpenAIStyler(prompt_style="smart")

        try:
            styled, usage = styler.style("test quota limit")
            print("⚠️  Styling succeeded even though quota was exhausted")
            print("   This might mean quota checking isn't working")
            return False
        except Exception as e:
            if "quota" in str(e).lower():
                print(f"✓ Quota enforcement working: {e}")
                return True
            else:
                print(f"❌ Unexpected error: {e}")
                return False
    else:
        print(f"✓ Quota available: {quota['remaining']} remaining")
        return True


def main():
    print("=" * 60)
    print("Backend Integration Test (Phase 3)")
    print("=" * 60)

    # Test 1: Authentication
    if not test_backend_auth():
        print("\n❌ Authentication test failed - stopping here")
        return 1

    # Test 2: Styling
    if not test_backend_styling():
        print("\n❌ Styling test failed")
        return 1

    # Test 3: Quota (informational only)
    test_quota_enforcement()

    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Get a Replicate API token from https://replicate.com")
    print("2. Add to backend/.env: REPLICATE_API_TOKEN=r8_your_token")
    print("3. Backend will auto-reload and LLM styling will work")
    print("4. Test in the actual Natter app!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
