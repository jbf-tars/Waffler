"""Making the dictation hotkey's listener again, one request at a time.

A change of the hotkey, the cancel key or the Command shortcut makes the app
build a new listener (on a Mac each one owns an event tap). Two changes in
quick succession, such as a double-click on a preset in Settings, used to run
two rebuilds side by side: the second stopped the listener the first had
already stopped, and both built a new one. Two taps in one process is the old
v3.14.13 segfault, and two Windows hooks fire every key twice.

``ListenerSwap`` is the rule that prevents it. Every request takes a number
(``ask``); a rebuild takes its ``turn`` under a lock, so only one runs at a
time, and a request that has been overtaken by a newer one steps aside (the
newer one builds from the settings as they are by then). The app does the
stopping, building and starting inside the turn.
"""
import threading
from contextlib import contextmanager


class ListenerSwap:
    def __init__(self):
        self._turn = threading.Lock()           # one rebuild at a time
        self._numbers = threading.Lock()
        self._newest = 0

    def ask(self) -> int:
        """Note that the listener is to be made again, and get this request's number."""
        with self._numbers:
            self._newest += 1
            return self._newest

    @contextmanager
    def turn(self, mine: int):
        """Wait for the rebuild that is under way, if any, and then hold the turn.
        Yields True when ``mine`` is still the newest request (go ahead), False
        when a newer one has been made since (do nothing: it will do this)."""
        with self._turn:
            with self._numbers:
                newest = mine == self._newest
            yield newest


SWAP = ListenerSwap()
