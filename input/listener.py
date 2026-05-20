"""
Global input listener — keyboard AND mouse buttons.
- All key presses (no filter) emit key_pressed("key") or key_pressed("space").
- Any mouse button press emits key_pressed("mouse").
- Key/button hold repeat suppressed: one emit per physical press until released.
"""
from PyQt5.QtCore import QObject, pyqtSignal
from pynput import keyboard, mouse


class InputListener(QObject):
    key_pressed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kb_listener:    keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener    | None = None
        self._held: set[str] = set()   # currently-held key/button IDs

    def start(self):
        self._kb_listener = keyboard.Listener(
            on_press   = self._on_key_press,
            on_release = self._on_key_release,
        )
        self._kb_listener.daemon = True
        self._kb_listener.start()

        self._mouse_listener = mouse.Listener(
            on_click = self._on_mouse_click,
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
