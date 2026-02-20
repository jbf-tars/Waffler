"""
Natter System Tray for Windows
Hides terminal window and shows tray icon with menu.
"""
import sys
import os
import threading
import time
from pathlib import Path

# Only import on Windows
if sys.platform == "win32":
    import pystray
    from PIL import Image, ImageDraw


def create_icon_image():
    """Create a simple microphone icon for the tray."""
    # 64x64 icon
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    # Draw a simple microphone shape
    # Body (rounded rectangle)
    draw.rounded_rectangle([20, 10, 44, 40], radius=8, fill=(52, 152, 219))
    # Stand
    draw.line([32, 40, 32, 50], fill=(52, 152, 219), width=3)
    draw.line([24, 50, 40, 50], fill=(52, 152, 219), width=3)
    
    return image


class SystemTrayManager:
    """Manages the system tray icon and menu."""
    
    def __init__(self, on_open_ui=None, on_quit=None):
        self.on_open_ui = on_open_ui
        self.on_quit = on_quit
        self.icon = None
        self._running = False
    
    def create_menu(self):
        """Create the tray menu."""
        menu = pystray.Menu(
            pystray.MenuItem("Open Natter", self._on_open_ui),
            pystray.MenuItem("Quit", self._on_quit),
        )
        return menu
    
    def _on_open_ui(self, icon, item):
        """Handle Open UI click."""
        if self.on_open_ui:
            self.on_open_ui()
    
    def _on_quit(self, icon, item):
        """Handle Quit click."""
        self._running = False
        if self.on_quit:
            self.on_quit()
    
    def start(self):
        """Start the system tray icon."""
        if sys.platform != "win32":
            return
        
        self._running = True
        
        # Create icon
        image = create_icon_image()
        
        # Create and run the icon
        self.icon = pystray.Icon(
            "Natter",
            image,
            "Natter - Running",
            self.create_menu()
        )
        
        # Run in a separate thread so it doesn't block
        def run_icon():
            self.icon.run()
        
        thread = threading.Thread(target=run_icon, daemon=True)
        thread.start()
        
        # Hide console window on Windows
        if sys.platform == "win32":
            import ctypes
            whnd = ctypes.windll.kernel32.GetConsoleWindow()
            if whnd:
                ctypes.windll.user32.ShowWindow(whnd, 0)  # 0 = SW_HIDE
    
    def stop(self):
        """Stop the system tray icon."""
        self._running = False
        if self.icon:
            self.icon.stop()


def hide_console_window():
    """Hide the console window on Windows."""
    if sys.platform == "win32":
        import ctypes
        whnd = ctypes.windll.kernel32.GetConsoleWindow()
        if whnd:
            ctypes.windll.user32.ShowWindow(whnd, 0)  # 0 = SW_HIDE


def show_console_window():
    """Show the console window on Windows (for debugging)."""
    if sys.platform == "win32":
        import ctypes
        whnd = ctypes.windll.kernel32.GetConsoleWindow()
        if whnd:
            ctypes.windll.user32.ShowWindow(whnd, 1)  # 1 = SW_SHOW


if __name__ == "__main__":
    # Test the tray
    print("Testing system tray...")
    
    def test_open():
        print("Open UI clicked!")
    
    def test_quit():
        print("Quit clicked!")
        import sys
        sys.exit(0)
    
    tray = SystemTrayManager(on_open_ui=test_open, on_quit=test_quit)
    tray.start()
    
    print("Tray running. Click menu to test.")
    print("Press Ctrl+C to quit.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting...")
        tray.stop()
