#!/usr/bin/env python3
"""
Test suite for Wispr Flow-inspired improvements to Waffler
Compares Normal mode vs Normal (Wispr) mode
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from style_openai import OpenAIStyler
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Test cases comparing behavior
TEST_CASES = [
    {
        "name": "Course Correction - Date Change",
        "input": "Let's schedule the meeting for tomorrow, wait no, make that Friday at 3pm instead",
        "expected_wispr": "Let's schedule the meeting for Friday at 3pm",
        "notes": "Should use final corrected date (Friday) and remove 'wait no, instead'"
    },
    {
        "name": "Course Correction - Price Change",
        "input": "The total cost is $500, actually I meant $450 for the basic package",
        "expected_wispr": "The total cost is $450 for the basic package",
        "notes": "Should use final corrected price ($450)"
    },
    {
        "name": "Course Correction - Name Change",
        "input": "Send the report to John, uh I mean Jane Smith in marketing",
        "expected_wispr": "Send the report to Jane Smith in marketing",
        "notes": "Should use corrected name (Jane) and remove 'uh I mean'"
    },
    {
        "name": "Filler Removal - Heavy Fillers",
        "input": "Um, so basically I think we should like, you know, move forward with the project",
        "expected_wispr": "We should move forward with the project",
        "notes": "Should remove um, so, basically, like, you know while preserving meaning"
    },
    {
        "name": "Context Preservation",
        "input": "As you can see in the screenshot um the button is like not working properly",
        "expected_wispr": "As you can see in the screenshot, the button is not working properly",
        "notes": "Should preserve 'as you can see in the screenshot' while removing fillers"
    },
    {
        "name": "Meaningful Qualifier Preservation",
        "input": "I think this approach might be better for performance",
        "expected_wispr": "I think this approach might be better for performance",
        "notes": "Should preserve 'I think' and 'might' as they express uncertainty"
    },
    {
        "name": "Complex Ramble with Multiple Issues",
        "input": "So um the the database query is like really slow basically we need to optimize it tomorrow no wait today before 5pm",
        "expected_wispr": "The database query is really slow. We need to optimize it today before 5pm.",
        "notes": "Should remove fillers, fix repetition, use corrected time"
    },
    {
        "name": "Technical Context",
        "input": "In the API response um the status code is like 500 basically internal server error",
        "expected_wispr": "In the API response, the status code is 500 - internal server error",
        "notes": "Should preserve technical terms and context"
    },
    {
        "name": "Stuttered Repetition",
        "input": "The the performance metrics show that that we need more memory",
        "expected_wispr": "The performance metrics show that we need more memory",
        "notes": "Should remove stuttered repetitions 'the the' and 'that that'"
    },
    {
        "name": "Redundant Hedge Words",
        "input": "I think maybe possibly we should probably consider optimizing this",
        "expected_wispr": "We should consider optimizing this",
        "notes": "Should remove redundant hedge words while preserving core message"
    },
]


def run_comparison_test(api_key: str, groq_key: str = ""):
    """Run comparison between normal and wispr modes"""

    print("=" * 80)
    print("WISPR FLOW IMPROVEMENTS TEST SUITE")
    print("=" * 80)
    print()

    # Create stylers for both modes
    normal_styler = OpenAIStyler(
        api_key=api_key,
        model="gpt-4o-mini",
        prompt_style="normal",
        groq_api_key=groq_key
    )

    wispr_styler = OpenAIStyler(
        api_key=api_key,
        model="gpt-4o-mini",
        prompt_style="normal_wispr",
        groq_api_key=groq_key
    )

    results = []

    for i, test in enumerate(TEST_CASES, 1):
        print(f"\n{'─' * 80}")
        print(f"TEST {i}/{len(TEST_CASES)}: {test['name']}")
        print(f"{'─' * 80}")
        print(f"\n📝 INPUT:")
        print(f"   {test['input']}")
        print(f"\n💡 NOTES: {test['notes']}")

        # Test Normal mode
        print(f"\n🔵 NORMAL MODE:")
        try:
            normal_output, normal_usage = normal_styler.style(test['input'])
            print(f"   {normal_output}")
            if normal_usage.get('api_used'):
                provider = normal_usage.get('provider', 'unknown')
                print(f"   (Used {provider} API: {normal_usage['input_tokens']} in, {normal_usage['output_tokens']} out)")
        except Exception as e:
            normal_output = f"ERROR: {e}"
            print(f"   ❌ {e}")

        # Test Wispr mode
        print(f"\n🟣 WISPR MODE:")
        try:
            wispr_output, wispr_usage = wispr_styler.style(test['input'])
            print(f"   {wispr_output}")
            if wispr_usage.get('api_used'):
                provider = wispr_usage.get('provider', 'unknown')
                print(f"   (Used {provider} API: {wispr_usage['input_tokens']} in, {wispr_usage['output_tokens']} out)")
        except Exception as e:
            wispr_output = f"ERROR: {e}"
            print(f"   ❌ {e}")

        # Show expected
        if 'expected_wispr' in test:
            print(f"\n✅ EXPECTED (Wispr):")
            print(f"   {test['expected_wispr']}")

        # Compare
        if 'ERROR' not in wispr_output and 'expected_wispr' in test:
            match = wispr_output.strip() == test['expected_wispr'].strip()
            status = "✅ MATCH" if match else "⚠️  CLOSE (review manually)"
            print(f"\n{status}")

        results.append({
            'test': test['name'],
            'normal': normal_output,
            'wispr': wispr_output,
            'input': test['input']
        })

        print()

    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"\nTotal tests run: {len(TEST_CASES)}")
    print("\nThe Wispr mode improvements should show:")
    print("  ✅ Better course correction handling")
    print("  ✅ More intelligent filler removal")
    print("  ✅ Context preservation")
    print("  ✅ Natural punctuation")
    print()

    return results


def main():
    """Entry point"""
    api_key = os.getenv('OPENAI_API_KEY')
    groq_key = os.getenv('GROQ_API_KEY', '')

    if not api_key and not groq_key:
        print("❌ Error: No API key found")
        print("Set OPENAI_API_KEY or GROQ_API_KEY in your .env file")
        sys.exit(1)

    if groq_key:
        print("⚡ Using Groq for fast processing")
    elif api_key:
        print("🔑 Using OpenAI")

    run_comparison_test(api_key, groq_key)


if __name__ == "__main__":
    main()
