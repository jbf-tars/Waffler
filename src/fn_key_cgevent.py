"""
Backward-compat shim for ``FnKeyMonitor``.

The real implementation now lives in ``mac_hotkey_monitor`` as
``MacEventTap`` + ``FnHandler`` + ``SpaceHandler``. This file is kept only
so ``app.py``'s startup permission probe (``FnKeyMonitor(...)`` to trigger
the macOS Input Monitoring TCC prompt) and ``tests/test_fn_key.py`` keep
working without edits.

Critically, ``FnKeyMonitor`` here creates its OWN ``MacEventTap``. The
segfault fix in v3.14.13 is about not running multiple ``CGEventTap``
instances *concurrently* — the permission probe runs at startup before the
real ``SmartHotkeyListener`` is created, so it's sequential and safe.
"""

from __future__ import annotations

from typing import Callable, Optional

from mac_hotkey_monitor import MacEventTap, FnHandler, SpaceHandler


class FnKeyMonitor:
    """Drop-in replacement for the old ``FnKeyMonitor``.

    Internally just wires up a ``MacEventTap`` with an ``FnHandler`` and
    (optionally) a ``SpaceHandler``. Same public methods as before:
    ``start()``, ``stop()``, ``is_fn_pressed()``, plus the legacy
    ``_fn_pressed`` attribute access that the wizard code checks via
    ``getattr``.
    """

    def __init__(
        self,
        on_fn_press: Callable[[], None],
        on_fn_release: Callable[[], None],
        on_space_press: Optional[Callable[[], None]] = None,
    ) -> None:
        self._tap = MacEventTap()
        self._fn_handler = FnHandler(on_press=on_fn_press, on_release=on_fn_release)
        self._tap.add_handler(self._fn_handler)
        if on_space_press is not None:
            self._tap.add_handler(
                SpaceHandler(on_press=on_space_press, fn_handler=self._fn_handler)
            )

    def start(self) -> None:
        self._tap.start()

    def stop(self) -> None:
        self._tap.stop()

    def is_fn_pressed(self) -> bool:
        return self._fn_handler.is_pressed

    # Legacy attribute access used by app.py's get_fn_key_state()
    @property
    def _fn_pressed(self) -> bool:
        return self._fn_handler.is_pressed
