"""
Global input listener — keyboard AND mouse buttons.
- All key presses (no filter) emit key_pressed("key") or key_pressed("space").
- Any mouse button press emits key_pressed("mouse").
- Key/button hold repeat suppressed: one emit per physical press until released.

Art mode (美術模式):
- When state.art_mode is True (and state.art_always_on OR foreground window is a
  known design app/site), left-mouse drag emits key_pressed("key") at a rate
  controlled by state.art_drag_px (pixels per virtual click) and
  state.art_drag_max_cps (max virtual clicks per second).
- Foreground-window detection is cached for 250 ms to keep CPU impact negligible.
"""
import math
import time
import ctypes
import ctypes.wintypes

from PyQt5.QtCore import QObject, pyqtSignal
from pynput import keyboard, mouse

# ── Art-window detection ──────────────────────────────────────────────────────

# Native app executable names (lower-case, no path)
_ART_PROC = {
    "photoshop.exe",
    "illustrator.exe",
    "afterfx.exe",         # After Effects
    "premiere.exe",        # older Premiere
    "premierepro.exe",     # newer Premiere
}

# Substrings that must appear in the window title (lower-case)
_ART_TITLE = ("figma", "canva")

_art_cache_result: bool  = False
_art_cache_time:   float = 0.0
_ART_CACHE_TTL             = 0.25   # seconds between re-checks


def _detect_art_window() -> bool:
    """Return True if the foreground window belongs to a design tool."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return False

        # 1. Window title check — catches browser-based tools (Figma, Canva)
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title_lower = buf.value.lower()
            for kw in _ART_TITLE:
                if kw in title_lower:
                    return True

        # 2. Process-name check — catches native apps (PS, AI, AE, PR)
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            hproc = ctypes.windll.kernel32.OpenProcess(
                0x1000,   # PROCESS_QUERY_LIMITED_INFORMATION
                False, pid.value,
            )
            if hproc:
                try:
                    buf2 = ctypes.create_unicode_buffer(260)
                    size = ctypes.wintypes.DWORD(260)
                    ctypes.windll.kernel32.QueryFullProcessImageNameW(
                        hproc, 0, buf2, ctypes.byref(size))
                    # Extract filename only (last segment after \ or /)
                    proc_lower = buf2.value.lower().replace("/", "\\").split("\\")[-1]
                    if proc_lower in _ART_PROC:
                        return True
                finally:
                    ctypes.windll.kernel32.CloseHandle(hproc)
    except Exception:
        pass
    return False


def _is_art_window() -> bool:
    """Cached version of _detect_art_window — re-checks at most every 250 ms."""
    global _art_cache_result, _art_cache_time
    now = time.monotonic()
    if now - _art_cache_time < _ART_CACHE_TTL:
        return _art_cache_result
    _art_cache_time   = now
    _art_cache_result = _detect_art_window()
    return _art_cache_result


# ── Listener ──────────────────────────────────────────────────────────────────

class InputListener(QObject):
    key_pressed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kb_listener:    keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener    | None = None
        self._held: set[str] = set()   # currently-held key/button IDs

        # Reference to GameState — set via set_state() before start()
        self._state = None

        # Art-mode drag tracking (accessed only from pynput thread)
        self._art_last_x:    float = 0.0
        self._art_last_y:    float = 0.0
        self._art_accum:     float = 0.0   # accumulated px since last virtual click
        self._art_last_emit: float = 0.0   # monotonic time of last virtual click emit

    def set_state(self, state) -> None:
        """Attach the GameState so drag detection can read art_mode parameters."""
        self._state = state

    def start(self):
        self._kb_listener = keyboard.Listener(
            on_press   = self._on_key_press,
            on_release = self._on_key_release,
        )
        self._kb_listener.daemon = True
        self._kb_listener.start()

        self._mouse_listener = mouse.Listener(
            on_click  = self._on_mouse_click,
            on_move   = self._on_mouse_move,
            on_scroll = self._on_mouse_scroll,
        )
        self._mouse_listener.daemon = True
        self._mouse_listener.start()

    def stop(self):
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        self._held.clear()

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def _on_key_press(self, key):
        try:
            kid = _key_id(key)
            if kid in self._held:
                return
            self._held.add(kid)
            if key == keyboard.Key.space:
                self.key_pressed.emit("space")
            else:
                self.key_pressed.emit("key")
        except Exception:
            pass

    def _on_key_release(self, key):
        try:
            self._held.discard(_key_id(key))
        except Exception:
            pass

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def _on_mouse_click(self, x, y, button, pressed):
        try:
            bid = f"mouse:{button}"
            if pressed:
                if bid in self._held:
                    return
                self._held.add(bid)
                self.key_pressed.emit("mouse")
            else:
                self._held.discard(bid)
                # Reset drag accumulation when all inputs released
                if not self._held:
                    self._art_accum = 0.0
        except Exception:
            pass

    def _on_mouse_move(self, x, y):
        """Art-mode drag handler: any held key/button + drag → virtual clicks."""
        try:
            fx, fy = float(x), float(y)
            dx = fx - self._art_last_x
            dy = fy - self._art_last_y
            self._art_last_x = fx
            self._art_last_y = fy

            # Only accumulate when something is held and art mode is active
            if not self._held:
                return
            s = self._state
            if s is None or not s.art_mode:
                return
            if not s.art_always_on and not _is_art_window():
                return

            self._art_accum += math.sqrt(dx * dx + dy * dy)
            threshold    = max(1, s.art_drag_px)
            min_interval = 1.0 / max(0.1, s.art_drag_max_cps)

            while self._art_accum >= threshold:
                self._art_accum -= threshold
                now = time.monotonic()
                if now - self._art_last_emit >= min_interval:
                    self._art_last_emit = now
                    self.key_pressed.emit("key")
                else:
                    break   # rate-limited; keep remainder for next move event
        except Exception:
            pass

    def _on_mouse_scroll(self, x, y, dx, dy):
        """Art-mode scroll handler: each scroll tick → rate-limited virtual click."""
        try:
            s = self._state
            if s is None or not s.art_mode:
                return
            if not s.art_always_on and not _is_art_window():
                return
            min_interval = 1.0 / max(0.1, s.art_drag_max_cps)
            now = time.monotonic()
            if now - self._art_last_emit >= min_interval:
                self._art_last_emit = now
                self.key_pressed.emit("key")
        except Exception:
            pass


# Stable hashable ID for any keyboard key
def _key_id(key) -> str:
    if isinstance(key, keyboard.Key):
        return f"Key.{key.name}"
    try:
        return f"vk:{key.vk}"
    except AttributeError:
        pass
    try:
        return f"char:{key.char}"
    except AttributeError:
        pass
    return str(key)


# Keep old name as alias so existing imports don't break
KeyboardListener = InputListener
