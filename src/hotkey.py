"""Hotkey listener using pynput with support for key combinations"""

from pynput import keyboard
from typing import Callable, Optional, Set
import threading


class HotkeyListener:
    """Listens for hotkey combination press/release events"""
    
    def __init__(self, combination: str, on_press: Callable, on_release: Callable):
        """
        Initialize hotkey listener with key combination
        
        Args:
            combination: String like "cmd+shift+space" or "f13"
            on_press: Callback when combination is pressed
            on_release: Callback when combination is released
        """
        self.combination_str = combination.lower()
        self.on_press_callback = on_press
        self.on_release_callback = on_release
        self.listener: Optional[keyboard.Listener] = None
        self._is_active = False
        
        # Parse combination into required keys
        self.required_keys = self._parse_combination(combination)
        self.current_keys: Set = set()
        
    def _parse_combination(self, combination: str) -> Set:
        """Parse combination string into set of pynput Key objects"""
        keys = set()
        parts = [p.strip() for p in combination.lower().split('+')]
        
        for part in parts:
            key = self._parse_single_key(part)
            if key is not None:
                keys.add(key)
            else:
                raise ValueError(f"Invalid key in combination: {part}")
                
        return keys
        
    def _parse_single_key(self, key: str):
        """Parse single key string to pynput Key object"""
        # Function keys
        if key.startswith('f') and key[1:].isdigit():
            fn_num = int(key[1:])
            if 1 <= fn_num <= 20:
                return getattr(keyboard.Key, f'f{fn_num}', None)
                
        # Special keys mapping
        special_keys = {
            'space': keyboard.Key.space,
            'enter': keyboard.Key.enter,
            'return': keyboard.Key.enter,
            'shift': keyboard.Key.shift,
            'shift_l': keyboard.Key.shift_l,
            'shift_r': keyboard.Key.shift_r,
            'ctrl': keyboard.Key.ctrl,
            'control': keyboard.Key.ctrl,
            'ctrl_l': keyboard.Key.ctrl_l,
            'ctrl_r': keyboard.Key.ctrl_r,
            'cmd': keyboard.Key.cmd,
            'command': keyboard.Key.cmd,
            'cmd_l': keyboard.Key.cmd_l,
            'cmd_r': keyboard.Key.cmd_r,
            'alt': keyboard.Key.alt,
            'option': keyboard.Key.alt,
            'alt_l': keyboard.Key.alt_l,
            'alt_r': keyboard.Key.alt_r,
            'esc': keyboard.Key.esc,
            'escape': keyboard.Key.esc,
            'tab': keyboard.Key.tab,
            'backspace': keyboard.Key.backspace,
            'delete': keyboard.Key.delete,
        }
        
        if key in special_keys:
            return special_keys[key]
            
        # Single character
        if len(key) == 1:
            return keyboard.KeyCode.from_char(key)
            
        return None
    
    def _normalize_key(self, key):
        """Normalize modifier keys (e.g., shift_l -> shift)"""
        # Map left/right modifiers to generic versions for comparison
        if hasattr(key, 'value'):
            if key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
                return keyboard.Key.shift
            elif key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                return keyboard.Key.ctrl
            elif key in (keyboard.Key.cmd_l, keyboard.Key.cmd_r):
                return keyboard.Key.cmd
            elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
                return keyboard.Key.alt
        return key
        
    def _check_combination(self) -> bool:
        """Check if current keys match required combination"""
        normalized_current = {self._normalize_key(k) for k in self.current_keys}
        return self.required_keys.issubset(normalized_current)
        
    def _on_press(self, key):
        """Internal press handler"""
        self.current_keys.add(key)
        
        # Check if combination is now active
        if self._check_combination() and not self._is_active:
            self._is_active = True
            self.on_press_callback()
            
    def _on_release(self, key):
        """Internal release handler"""
        self.current_keys.discard(key)
        
        # Check if combination is no longer active
        if not self._check_combination() and self._is_active:
            self._is_active = False
            self.on_release_callback()
            
    def start(self):
        """Start listening for hotkey events"""
        if not self.required_keys:
            raise ValueError(f"No valid keys in combination: {self.combination_str}")
            
        combo_display = ' + '.join([
            k.name if hasattr(k, 'name') else str(k) 
            for k in sorted(self.required_keys, key=lambda x: str(x))
        ])
        print(f"⌨️  Hotkey listener started: {combo_display}")
        
        self.listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release
        )
        self.listener.start()
        
    def stop(self):
        """Stop listening for hotkey events"""
        if self.listener:
            self.listener.stop()
            print("⌨️  Hotkey listener stopped")
            
    def join(self):
        """Block until listener thread terminates"""
        if self.listener:
            self.listener.join()
