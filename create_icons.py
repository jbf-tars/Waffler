#!/usr/bin/env python3
"""Generate Waffler app icons from SVG"""

from PIL import Image, ImageDraw
import subprocess
import shutil
from pathlib import Path

# Golden waffle colors
WAFFLE_BG = "#C8A256"
WAFFLE_BORDER = "#8B6914"
CREAM = "#FFFDF5"

def create_waffle_icon(size):
    """Create a waffle icon with voice bars at given size"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Waffle rounded square background
    padding = size * 0.0625  # 4px at 64px
    waffle_rect = [padding, padding, size - padding, size - padding]
    corner_radius = size * 0.125  # 8px at 64px

    # Draw waffle base
    draw.rounded_rectangle(waffle_rect, radius=corner_radius, fill=WAFFLE_BG)
    draw.rounded_rectangle(waffle_rect, radius=corner_radius, outline=WAFFLE_BORDER, width=max(2, size // 32))

    # Voice bars (waveform) - cream colored
    bar_width = size * 0.125  # 8px at 64px
    center_x = size // 2
    center_y = size // 2

    # Center bar (tallest)
    bar_h = size * 0.5625  # 36px at 64px
    draw.rounded_rectangle(
        [center_x - bar_width//2, center_y - bar_h//2,
         center_x + bar_width//2, center_y + bar_h//2],
        radius=bar_width//2,
        fill=CREAM
    )

    # Left bars
    bar_spacing = size * 0.15625  # 10px at 64px

    # Left bar 1
    bar_h = size * 0.375  # 24px at 64px
    bar_w = size * 0.09375  # 6px at 64px
    x = center_x - bar_spacing - bar_w//2
    draw.rounded_rectangle(
        [x - bar_w//2, center_y - bar_h//2,
         x + bar_w//2, center_y + bar_h//2],
        radius=bar_w//2,
        fill=CREAM
    )

    # Left bar 2
    bar_h = size * 0.1875  # 12px at 64px
    bar_w = size * 0.078125  # 5px at 64px
    x = center_x - bar_spacing * 2 - bar_w//2
    draw.rounded_rectangle(
        [x - bar_w//2, center_y - bar_h//2,
         x + bar_w//2, center_y + bar_h//2],
        radius=bar_w//2,
        fill=CREAM
    )

    # Right bars (mirror)
    bar_h = size * 0.375
    bar_w = size * 0.09375
    x = center_x + bar_spacing + bar_w//2
    draw.rounded_rectangle(
        [x - bar_w//2, center_y - bar_h//2,
         x + bar_w//2, center_y + bar_h//2],
        radius=bar_w//2,
        fill=CREAM
    )

    bar_h = size * 0.1875
    bar_w = size * 0.078125
    x = center_x + bar_spacing * 2 + bar_w//2
    draw.rounded_rectangle(
        [x - bar_w//2, center_y - bar_h//2,
         x + bar_w//2, center_y + bar_h//2],
        radius=bar_w//2,
        fill=CREAM
    )

    return img

# Generate PNGs for iconset
iconset_dir = Path("icon.iconset")
iconset_dir.mkdir(exist_ok=True)

sizes = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_64x64.png": 64,
    "icon_64x64@2x.png": 128,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

print("Generating icon PNGs...")
for filename, size in sizes.items():
    img = create_waffle_icon(size)
    img.save(iconset_dir / filename)
    print(f"  ✓ {filename} ({size}x{size})")

# Save standalone 512px icon
icon_512 = create_waffle_icon(512)
icon_512.save("icon_512.png")
print("  ✓ icon_512.png")

# Generate .icns for Mac using iconutil (built into macOS)
print("\nGenerating icon.icns...")
try:
    subprocess.run(["iconutil", "-c", "icns", "icon.iconset", "-o", "icon.icns"], check=True)
    print("  ✓ icon.icns created")
except Exception as e:
    print(f"  ✗ Failed to create icon.icns: {e}")

# Generate .ico for Windows (multiple sizes)
print("\nGenerating icon.ico...")
try:
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_images = [create_waffle_icon(size[0]) for size in ico_sizes]
    ico_images[0].save("icon.ico", format='ICO', sizes=ico_sizes, append_images=ico_images[1:])
    print("  ✓ icon.ico created")
except Exception as e:
    print(f"  ✗ Failed to create icon.ico: {e}")

print("\n✅ All icons generated successfully!")
print("\nIcon locations:")
print("  - icon.icns (Mac app icon)")
print("  - icon.ico (Windows app icon)")
print("  - icon_512.png (standalone PNG)")
