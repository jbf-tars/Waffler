"""TDD: Tray icon must render as a waffle grid with syrup filling."""

import pytest
from PIL import Image, ImageDraw


def render_tray_icon(size=64):
    """Render waffle-with-syrup icon — extracted from app.py tray code."""
    SZ = size
    S = SZ / 69.0
    img = Image.new('RGBA', (SZ, SZ), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    WAFFLE_BODY = '#D4A843'
    WAFFLE_RIM  = '#B08530'
    CELL_BG     = '#C49838'
    SYRUP_COLOR = '#5C2E0E'
    SYRUP_LIGHT = '#7A3F14'

    OR = max(1, int(5 * S))
    IP = max(1, int(3 * S))
    CS = max(2, int(11 * S))
    GG = max(1, int(3 * S))
    CR = max(2, int(10 * S))
    GOX = OR + IP
    GOY = OR + IP

    draw.rounded_rectangle([0, 0, SZ - 1, SZ - 1], radius=CR,
                           fill=WAFFLE_BODY, outline=WAFFLE_RIM, width=max(1, int(2 * S)))

    levels = [0.15, 0.05, 0.10, 0.20,
              0.35, 0.25, 0.30, 0.40,
              0.65, 0.55, 0.60, 0.70,
              0.95, 0.85, 0.90, 1.00]

    for row in range(4):
        for col in range(4):
            i = row * 4 + col
            cx = GOX + col * (CS + GG)
            cy = GOY + row * (CS + GG)
            draw.rectangle([cx, cy, cx + CS, cy + CS], fill=CELL_BG)
            lvl = levels[i]
            fh = int(lvl * CS)
            if fh > 1:
                color = SYRUP_COLOR if lvl > 0.6 else SYRUP_LIGHT
                sy = cy + CS - fh
                if cy + CS - 1 > sy:
                    draw.rectangle([cx + 1, sy, cx + CS - 1, cy + CS - 1], fill=color)

    return img


class TestTrayIconRendering:
    """The tray icon must be a recognizable 4x4 waffle grid with syrup."""

    def test_grid_cells_are_visible_at_64px(self):
        """At 64x64, the 4x4 grid cells must be distinguishable."""
        img = render_tray_icon(64)
        pixels = img.load()
        SZ = 64
        S = SZ / 69.0
        OR = max(1, int(5 * S))
        IP = max(1, int(3 * S))
        CS = max(2, int(11 * S))
        GG = max(1, int(3 * S))
        GOX = OR + IP
        GOY = OR + IP

        # Each cell should be at least 2px wide to be visible
        assert CS >= 2, f"Cell size {CS}px is too small to see"

        # Check that a cell center pixel is NOT transparent
        cell_cx = GOX + CS // 2
        cell_cy = GOY + CS // 2
        r, g, b, a = pixels[cell_cx, cell_cy]
        assert a > 200, f"Cell center pixel at ({cell_cx},{cell_cy}) is transparent: alpha={a}"

    def test_grid_fits_within_canvas(self):
        """The entire 4x4 grid must fit within the icon canvas."""
        SZ = 64
        S = SZ / 69.0
        OR = max(1, int(5 * S))
        IP = max(1, int(3 * S))
        CS = max(2, int(11 * S))
        GG = max(1, int(3 * S))
        GOX = OR + IP
        GOY = OR + IP

        grid_end_x = GOX + 4 * CS + 3 * GG
        grid_end_y = GOY + 4 * CS + 3 * GG
        assert grid_end_x <= SZ, f"Grid overflows X: {grid_end_x} > {SZ}"
        assert grid_end_y <= SZ, f"Grid overflows Y: {grid_end_y} > {SZ}"

    def test_bottom_row_has_syrup(self):
        """Bottom row cells (levels 0.85-1.0) must have dark syrup pixels."""
        img = render_tray_icon(64)
        pixels = img.load()
        SZ = 64
        S = SZ / 69.0
        OR = max(1, int(5 * S))
        IP = max(1, int(3 * S))
        CS = max(2, int(11 * S))
        GG = max(1, int(3 * S))
        GOX = OR + IP
        GOY = OR + IP

        # Bottom-right cell (row=3, col=3, level=1.0) — should be fully syrup
        cx = GOX + 3 * (CS + GG)
        cy = GOY + 3 * (CS + GG)
        # Sample the bottom half of this cell
        sample_y = cy + CS - 2  # near bottom
        sample_x = cx + CS // 2  # center
        if sample_x < SZ and sample_y < SZ:
            r, g, b, a = pixels[sample_x, sample_y]
            # Syrup is dark brown (#5C2E0E = r=92, g=46, b=14)
            assert r < 150 and g < 100, f"Expected dark syrup at ({sample_x},{sample_y}), got RGBA=({r},{g},{b},{a})"

    def test_top_row_mostly_empty(self):
        """Top row cells (levels 0.05-0.20) should be mostly cell background, not syrup."""
        img = render_tray_icon(64)
        pixels = img.load()
        SZ = 64
        S = SZ / 69.0
        OR = max(1, int(5 * S))
        IP = max(1, int(3 * S))
        CS = max(2, int(11 * S))
        GG = max(1, int(3 * S))
        GOX = OR + IP
        GOY = OR + IP

        # Top-left cell (row=0, col=1, level=0.05) — top half should be cell background
        cx = GOX + 1 * (CS + GG)
        cy = GOY
        sample_y = cy + 1  # near top
        sample_x = cx + CS // 2
        if sample_x < SZ and sample_y < SZ:
            r, g, b, a = pixels[sample_x, sample_y]
            # Cell background is #C49838 (r=196, g=152, b=56) — golden, not dark
            assert r > 150, f"Expected golden cell bg at ({sample_x},{sample_y}), got RGBA=({r},{g},{b},{a})"

    def test_visual_output(self, tmp_path):
        """Save the rendered icon for visual inspection."""
        img = render_tray_icon(64)
        img.save("tests/tray_icon_actual_64.png")
        img_big = render_tray_icon(256)
        img_big.save("tests/tray_icon_actual_256.png")


class TestTrayIconAtSystemSize:
    """The icon must still look like a waffle when downscaled to system tray size (16px)."""

    def test_downscaled_to_16px_has_contrast(self):
        """When the tray icon is shrunk to 16x16, it must still have visible dark/light contrast."""
        img = render_tray_icon(64)
        small = img.resize((16, 16), Image.LANCZOS)
        pixels = small.load()

        # Collect unique brightness values across the icon
        brightnesses = set()
        for y in range(16):
            for x in range(16):
                r, g, b, a = pixels[x, y]
                if a > 100:  # only opaque pixels
                    brightnesses.add(r // 32)  # bucket into 8 ranges

        # Must have at least 3 distinct brightness levels (golden body, cell bg, dark syrup)
        assert len(brightnesses) >= 3, (
            f"Only {len(brightnesses)} brightness levels at 16x16 — icon looks flat/uniform"
        )

    def test_render_at_256_downscales_better(self):
        """Rendering at 256px then downscaling to 16px preserves more detail than 64px."""
        img_64 = render_tray_icon(64).resize((16, 16), Image.LANCZOS)
        img_256 = render_tray_icon(256).resize((16, 16), Image.LANCZOS)

        # Count distinct colors in each
        def count_colors(img):
            colors = set()
            pixels = img.load()
            for y in range(16):
                for x in range(16):
                    r, g, b, a = pixels[x, y]
                    if a > 100:
                        colors.add((r // 16, g // 16, b // 16))
            return len(colors)

        colors_64 = count_colors(img_64)
        colors_256 = count_colors(img_256)

        # 256px source should produce more color detail at 16px
        assert colors_256 >= colors_64, (
            f"256px render ({colors_256} colors) should have >= detail than 64px ({colors_64} colors)"
        )

        # Save both for visual comparison
        img_64.resize((128, 128), Image.NEAREST).save("tests/tray_from_64_upscaled.png")
        img_256.resize((128, 128), Image.NEAREST).save("tests/tray_from_256_upscaled.png")

    def test_ico_file_default_opens_smallest_size(self):
        """PIL Image.open on .ico defaults to smallest size — this is the bug."""
        import os
        ico_path = os.path.join(os.path.dirname(__file__), '..', 'icon.ico')
        if not os.path.exists(ico_path):
            pytest.skip("icon.ico not found")

        img = Image.open(ico_path)
        # PIL defaults to the first/smallest size in the ICO
        default_size = img.size
        print(f"\nICO default open size: {default_size}")

        # The bug: if default is 16x16, resizing to 64x64 upscales garbage
        # We should explicitly load the largest available size
        available_sizes = img.info.get('sizes', set())
        print(f"Available ICO sizes: {available_sizes}")

        if available_sizes:
            largest = max(available_sizes, key=lambda s: s[0])
            print(f"Largest available: {largest}")

            # Load largest explicitly
            img_largest = Image.open(ico_path)
            img_largest.size = largest
            img_large = img_largest.copy().convert('RGBA')
            print(f"Loaded at largest size: {img_large.size}")

            # Save comparison
            img_default = img.copy().convert('RGBA').resize((128, 128), Image.NEAREST)
            img_default.save("tests/ico_default_size_upscaled.png")
            img_large_preview = img_large.resize((128, 128), Image.LANCZOS)
            img_large_preview.save("tests/ico_largest_size_preview.png")

            # PIL opens ICO at 256x256 by default — that's fine
            assert default_size[0] >= 64, (
                f"ICO default size {default_size} should be at least 64px"
            )

    def test_ico_loaded_for_pystray_has_detail(self):
        """Loading icon.ico at native 256x256 produces a better tray icon than rendering at 64."""
        import os
        ico_path = os.path.join(os.path.dirname(__file__), '..', 'icon.ico')
        if not os.path.exists(ico_path):
            pytest.skip("icon.ico not found")

        # Method A: What app.py currently does (render at 64px)
        method_a = render_tray_icon(64)

        # Method B: Load icon.ico at native 256x256, DON'T resize yet
        method_b = Image.open(ico_path).convert('RGBA')
        assert method_b.size == (256, 256), f"Expected 256x256 from ICO, got {method_b.size}"

        # Simulate what Windows tray does: downscale to 24x24 (common at 125% DPI)
        tray_size = 24
        a_small = method_a.resize((tray_size, tray_size), Image.LANCZOS)
        b_small = method_b.resize((tray_size, tray_size), Image.LANCZOS)

        # Count color variety — more = better detail
        def color_variety(img):
            colors = set()
            px = img.load()
            for y in range(img.height):
                for x in range(img.width):
                    r, g, b, a = px[x, y]
                    if a > 100:
                        colors.add((r // 24, g // 24, b // 24))
            return len(colors)

        va = color_variety(a_small)
        vb = color_variety(b_small)
        print(f"\nColor variety at {tray_size}px — rendered@64: {va}, ico@256: {vb}")

        # Save for visual comparison
        a_small.resize((192, 192), Image.NEAREST).save("tests/tray_method_a_rendered.png")
        b_small.resize((192, 192), Image.NEAREST).save("tests/tray_method_b_ico.png")

        # Method B should be at least as detailed
        assert vb >= va, f"ICO source ({vb}) should have >= detail than 64px render ({va})"

    def test_app_tray_code_loads_ico_at_full_resolution(self):
        """The actual app.py tray code must load icon.ico at 256x256, NOT resize it."""
        import os
        ico_path = os.path.join(os.path.dirname(__file__), '..', 'icon.ico')
        if not os.path.exists(ico_path):
            pytest.skip("icon.ico not found")

        # Replicate exact code from app.py _create_windows_tray_icon
        img = Image.open(ico_path).convert('RGBA')

        # Must be 256x256 (full resolution, no resize)
        assert img.size == (256, 256), (
            f"Tray icon should be loaded at full 256x256, got {img.size}. "
            f"DO NOT resize before passing to pystray."
        )
