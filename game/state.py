"""
GameState: physics simulation + keyboard state machine.
All coordinates are in 800×600 game space.

Modes:
  "combo"  — 連打模式: every key/click queues one strike, force +1 per hit
  "charge" — 蓄力模式: keys during strike accumulate charge, force +1+N per hit
"""
import math
import random
from save import load_save
from config import (
    GAME_W, GAME_H,
    AX, AY_BASE, FACE_TOP, FACE_L, FACE_R,
    KB_X, KB_Y, MAX_VCY, APPROACH_DIST,
    IDLE_ANGLE, SWING_ANGLE,
    HEAD_OFFSET, HEAD_THICK, HEAD_PERP,
    HL, HR, HP,
    KY, DY,
    TYPING_BASE_MS, TYPING_MAX_CHARGE,
    FEVER_THRESHOLD, FEVER_DURATION, FEVER_COOLDOWN,
    CHARGE_EX_LIFT, CHARGE_EX_IDLE_MS,
)

KX = 90
DX = 12


class Spark:
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life", "size", "color")

    def __init__(self, x, y, vx, vy, life, size, color):
        self.x, self.y   = x, y
        self.vx, self.vy = vx, vy
        self.life = life
        self.max_life = life
        self.size = size
        self.color = color   # (r, g, b)

    @property
    def frac(self):
        return max(0.0, self.life / self.max_life)


class GameState:
    def __init__(self):
        # Virtual cursor
        self.vcx: float  = float(KB_X)
        self.vcy: float  = float(KB_Y)
        self.vcvx: float = 0.0
        self.vcvy: float = 0.0
        self.vel_y: float = 0.0

        # Mouse anchor (fixed; widget is keyboard-only)
        self.mx: float = float(KB_X)
        self.my: float = float(KB_Y)

        # Hit state
        self.has_hit: bool       = False
        self.hit_cooldown: float = 0.0

        # ── Three counters (loaded from save) ──────────────────────────────
        _sv = load_save()
        self.hit_count:   int = int(_sv.get("hit_count",   0))
        self.force_count: int = int(_sv.get("force_count", 0))
        self.click_count: int = int(_sv.get("click_count", 0))

        self.last_force: int = 0

        # Visual
        self.strike_flash: float = 0.0
        self.anvil_glow:   float = 0.0
        self.sparks: list[Spark] = []

        # ── Keyboard / input state machine ─────────────────────────────────
        self.kb_active: bool = False
        self.kb_mode: str    = _sv.get("kb_mode", "combo")   # "combo" | "charge" | "charge_ex"
        self.kb_state: str   = "idle"     # "idle" | "strike" | "wait"

        # Combo mode: queued strikes
        self.space_queue: int = 0

        # Charge mode
        self.typing_pending:      bool  = False
        self.typing_wants_strike: bool  = False
        self.typing_charge:       int   = 0
        self.typing_base_ms:      float = TYPING_BASE_MS
        self.typing_max_charge:   int   = int(_sv.get("typing_max_charge", TYPING_MAX_CHARGE))
        self.typing_cooldown:     float = 0.0
        self.charge_pulses: list[dict]  = []

        # Charge-EX mode settings (saved)
        self.charge_ex_lift: float = float(_sv.get("charge_ex_lift", CHARGE_EX_LIFT))

        # Charge mode runtime state (transient, not saved)
        self.charge_ex_armed:      bool  = False   # slam timer is counting down
        self.charge_ex_timer:      float = 0.0     # ms remaining until hard-cap slam
        self.charge_ex_idle_timer: float = 0.0     # ms remaining until idle-triggered slam
        self.charge_prefire:       bool  = False   # pre-input: click registered during wait

        # KB activation anchor
        self._kb_start_mx: float = float(KB_X)
        self._kb_start_my: float = float(KB_Y)

        # ── UI / display settings ──────────────────────────────────────────
        self.ui_scale:       float = float(_sv.get("ui_scale",       0.6))
        self.show_hit:       bool  = bool(_sv.get("show_hit",        False))
        self.show_force:     bool  = bool(_sv.get("show_force",      False))
        self.show_click:     bool  = bool(_sv.get("show_click",      True))
        self.show_charge_bar: bool = bool(_sv.get("show_charge_bar", False))
        self.autostart:       bool = bool(_sv.get("autostart",       True))

        # Widget position (logical pixels).  None = let Qt decide on first launch.
        _wx = _sv.get("widget_x")
        _wy = _sv.get("widget_y")
        self.widget_x: int | None = int(_wx) if _wx is not None else None
        self.widget_y: int | None = int(_wy) if _wy is not None else None

        # ── Play time ──────────────────────────────────────────────────────
        self.play_time: float = float(_sv.get("play_time", 0.0))

        # ── Turbo / Fever mode (loaded from save) ──────────────────────────
        self.turbo_mode: bool               = bool(_sv.get("turbo_mode", False))
        self.fever_active: bool             = False
        self.fever_timer: float             = 0.0   # seconds remaining in fever
        self.fever_cooldown_timer: float    = 0.0   # cooldown seconds remaining
        self.consecutive_full_charge: int   = 0
        self.fever_threshold: int           = int(_sv.get("fever_threshold", FEVER_THRESHOLD))
        self.fever_duration: float          = float(_sv.get("fever_duration", FEVER_DURATION))
        self.fever_cooldown_duration: float = float(_sv.get("fever_cooldown_duration", FEVER_COOLDOWN))

    # ─────────────────────────────────────────────────────────────────────────
    # Geometry (exact port from JS)
    # ─────────────────────────────────────────────────────────────────────────

    def hammer_angle(self) -> float:
        dist = FACE_TOP - self.vcy
        t = 1.0 - max(0.0, min(1.0, dist / APPROACH_DIST))
        return IDLE_ANGLE + t * (SWING_ANGLE - IDLE_ANGLE)

    def head_face_pos(self) -> tuple[float, float]:
        a     = self.hammer_angle()
        cos_a = math.cos(a)
        sin_a = math.sin(a)
        hx = self.vcx + HEAD_OFFSET * cos_a + HEAD_PERP * sin_a
        hy = self.vcy + HEAD_OFFSET * sin_a - HEAD_PERP * cos_a
        return hx, hy

    def render_vcy(self) -> float:
        a     = self.hammer_angle()
        sin_a = math.sin(a)
        cos_a = math.cos(a)
        face_y = self.vcy + HEAD_OFFSET * sin_a - HEAD_PERP * cos_a
        face_x = self.vcx + HEAD_OFFSET * cos_a + HEAD_PERP * sin_a
        if face_y > FACE_TOP and FACE_L - 20 <= face_x <= FACE_R + 20:
            return FACE_TOP - HEAD_OFFSET * sin_a + HEAD_PERP * cos_a
        return self.vcy

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def on_key_event(self, key: str):
        """
        Called on any key-down (repeat already filtered by listener).
        Routes to combo or charge handler based on current mode.
        Always increments click_count.
        """
        self.click_count += 1
        if self.kb_mode == "combo":
            self._handle_combo_key()
        elif self.kb_mode == "charge_legacy":
            self._handle_charge_legacy_key()
        else:   # "charge" — the lift/auto-slam mode
            self._handle_charge_key()

    def update(self, delta_ms: float):
        """Advance by delta_ms. Returns (intensity, charge_mult) on hit, else None."""
        dt = min(delta_ms * 0.001, 0.05)

        self.play_time += dt

        if self.hit_cooldown    > 0: self.hit_cooldown    = max(0.0, self.hit_cooldown    - delta_ms)
        if self.typing_cooldown > 0: self.typing_cooldown = max(0.0, self.typing_cooldown - delta_ms)
        if self.strike_flash    > 0: self.strike_flash    = max(0.0, self.strike_flash    - dt * 5.0)
        if self.anvil_glow      > 0: self.anvil_glow      = max(0.0, self.anvil_glow      - dt * 4.0)

        # ── Charge auto-slam timers (lift mode) ───────────────────────────
        # Two independent triggers: hard-cap window OR inactivity gap.
        if (self.kb_mode == "charge"
                and self.charge_ex_armed
                and self.kb_state == "idle"
                and not self.typing_wants_strike):
            self.charge_ex_timer      = max(0.0, self.charge_ex_timer      - delta_ms)
            self.charge_ex_idle_timer = max(0.0, self.charge_ex_idle_timer - delta_ms)
            if self.charge_ex_timer <= 0.0 or self.charge_ex_idle_timer <= 0.0:
                self.charge_ex_armed      = False
                self.typing_wants_strike  = True   # triggers auto-slam via state machine

        # ── Fever timers ───────────────────────────────────────────────────
        if self.turbo_mode:
            if self.fever_active:
                self.fever_timer = max(0.0, self.fever_timer - dt)
                if self.fever_timer <= 0:
                    self._exit_fever()
            elif self.fever_cooldown_timer > 0:
                self.fever_cooldown_timer = max(0.0, self.fever_cooldown_timer - dt)

        # Advance pulse timers in-place (avoids creating new dicts every frame)
        _dt28 = dt * 2.8
        _pulses = self.charge_pulses
        i = len(_pulses) - 1
        while i >= 0:
            _pulses[i]["t"] += _dt28
            if _pulses[i]["t"] >= 1.0:
                _pulses.pop(i)
            i -= 1

        self._update_kb_state_machine()

        tx, ty = self._kb_target() if self.kb_active else (self.mx, self.my)
        spring_ty = min(ty, float(MAX_VCY)) if self.has_hit else ty

        # Softer spring in charge idle for floaty lift feel
        if self.kb_mode == "charge" and self.kb_active and self.kb_state == "idle":
            ky_eff = 50.0
        else:
            ky_eff = KY
        self.vcvx += (KX     * (tx        - self.vcx) - DX * self.vcvx) * dt
        self.vcvy += (ky_eff * (spring_ty - self.vcy) - DY * self.vcvy) * dt
        self.vcx  += self.vcvx * dt
        self.vcy  += self.vcvy * dt

        self.vel_y = self.vel_y * 0.4 + self.vcvy * 0.6

        over_anvil = FACE_L - 40 <= self.vcx <= FACE_R + 60
        max_vcy    = float(MAX_VCY)

        if self.vcy > max_vcy and (self.has_hit or self.kb_active) and over_anvil:
            self.vcy = max_vcy
            if self.vcvy > 0:
                self.vcvy = 0.0

        if self.has_hit and (not over_anvil or (self.vcy < max_vcy - 30 and ty < max_vcy)):
            self.has_hit = False

        hp_x, hp_y = self.head_face_pos()
        in_strike_zone = FACE_TOP - 20 <= hp_y <= FACE_TOP + 50
        on_anvil       = FACE_L  - 20 <= hp_x <= FACE_R  + 20
        hit_allowed    = (self.hit_cooldown <= 0
                          or (self.kb_active and self.kb_state == "strike"))

        hit_result = None
        if not self.has_hit and hit_allowed and self.vel_y > 200 and in_strike_zone and on_anvil:
            hit_result = self._on_hit(hp_x)

        # Pre-compute per-frame constants outside the loop
        _grav   = 870 * dt
        _decay  = 0.984 ** (dt * 60)
        _max_y  = GAME_H + 30
        alive = []
        for s in self.sparks:
            s.vy  += _grav
            s.vx  *= _decay
            s.x   += s.vx * dt
            s.y   += s.vy * dt
            s.life -= dt
            if s.life > 0 and s.y < _max_y:
                alive.append(s)
        self.sparks = alive

        return hit_result

    def to_save(self) -> dict:
        return {
            "hit_count":               self.hit_count,
            "force_count":             self.force_count,
            "click_count":             self.click_count,
            "play_time":               self.play_time,
            "kb_mode":                 self.kb_mode,
            "ui_scale":                self.ui_scale,
            "show_hit":                self.show_hit,
            "show_force":              self.show_force,
            "show_click":              self.show_click,
            "show_charge_bar":         self.show_charge_bar,
            "typing_max_charge":       self.typing_max_charge,
            "turbo_mode":              self.turbo_mode,
            "fever_threshold":         self.fever_threshold,
            "fever_duration":          self.fever_duration,
            "fever_cooldown_duration": self.fever_cooldown_duration,
            "autostart":               self.autostart,
            "charge_ex_lift":          self.charge_ex_lift,
            "widget_x":                self.widget_x,
            "widget_y":                self.widget_y,
        }

    def reset_save(self):
        """Clear all statistics and restore every setting to its default."""
        from config import TYPING_MAX_CHARGE, FEVER_THRESHOLD, FEVER_DURATION, FEVER_COOLDOWN
        # Statistics
        self.hit_count   = 0
        self.force_count = 0
        self.click_count = 0
        self.play_time   = 0.0
        # Input state
        self.kb_mode             = "combo"
        self.kb_active           = False
        self.kb_state            = "idle"
        self.space_queue         = 0
        self.typing_charge       = 0
        self.typing_pending      = False
        self.typing_wants_strike = False
        self.typing_cooldown     = 0.0
        self.typing_max_charge   = TYPING_MAX_CHARGE
        self.charge_pulses.clear()
        self.charge_ex_armed      = False
        self.charge_ex_timer      = 0.0
        self.charge_ex_idle_timer = 0.0
        self.charge_prefire       = False
        self.charge_ex_lift       = CHARGE_EX_LIFT
        # Turbo / fever
        self.turbo_mode              = False
        self.fever_active            = False
        self.fever_timer             = 0.0
        self.fever_cooldown_timer    = 0.0
        self.consecutive_full_charge = 0
        self.fever_threshold         = FEVER_THRESHOLD
        self.fever_duration          = FEVER_DURATION
        self.fever_cooldown_duration = FEVER_COOLDOWN
        # Display
        self.ui_scale        = 0.6
        self.show_hit        = False
        self.show_force      = False
        self.show_click      = True
        self.show_charge_bar = False
        self.autostart       = True
        self.widget_x        = None
        self.widget_y        = None
        # Visuals
        self.strike_flash = 0.0
        self.anvil_glow   = 0.0
        self.sparks.clear()

    # ─────────────────────────────────────────────────────────────────────────
    # Internal
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_combo_key(self):
        """連打模式: every press queues one strike.
        Overflow (queue full): force still accumulates, no extra strike queued.
        """
        if not self.kb_active:
            self.kb_active = True
            self.kb_mode   = "combo"
            self.kb_state  = "idle"
            self._kb_start_mx = self.mx
            self._kb_start_my = self.my
        if self.space_queue < 5:
            self.space_queue += 1
        else:
            # Queue full — count force without queuing another strike
            self.force_count += 1

    def _handle_charge_legacy_key(self):
        """蓄力模式(舊版): every key press contributes +1 charge to the upcoming hit."""
        if not self.kb_active:
            self.kb_active = True
            self.kb_mode   = "charge_legacy"
            self.kb_state  = "idle"
            self._kb_start_mx = self.mx
            self._kb_start_my = self.my

        if self.kb_mode != "charge_legacy":
            return

        state = self.kb_state
        if state == "idle":
            # Trigger click: start a new strike AND count as the first charge slot.
            self.typing_pending      = False
            self.typing_wants_strike = True
            self.typing_charge = min(self.typing_charge + 1, self.typing_max_charge)
            # No pulse yet — hammer hasn't started moving
        elif state == "strike":
            # Extra charge during the downswing — show pulse animation
            self.typing_charge = min(self.typing_charge + 1, self.typing_max_charge)
            cf = self.typing_charge / max(1, self.typing_max_charge)
            self.charge_pulses.append({"t": 0.0, "cf": cf})
        elif state == "wait":
            # Queue next strike AND pre-charge it; emit pulse for visual feedback
            self.typing_pending = True
            self.typing_charge  = min(self.typing_charge + 1, self.typing_max_charge)
            cf = self.typing_charge / max(1, self.typing_max_charge)
            self.charge_pulses.append({"t": 0.0, "cf": cf})

    def _handle_charge_key(self):
        """蓄力模式: clicks charge AND give an upward velocity kick.
        A fixed window timer starts on the first click; when it expires the
        hammer auto-slams regardless of charge level.  After the slam the
        player must click again to start the next cycle.
        """
        if not self.kb_active:
            self.kb_active = True
            self.kb_state  = "idle"
            self._kb_start_mx = self.mx
            self._kb_start_my = self.my

        if self.kb_state == "idle":
            # Arm the hard-cap window on first click (never reset by subsequent clicks)
            if not self.charge_ex_armed:
                self.charge_ex_armed = True
                self.charge_ex_timer = self.typing_base_ms   # hard-cap window
            # Reset inactivity timer on EVERY click — slam if player stops clicking
            self.charge_ex_idle_timer = CHARGE_EX_IDLE_MS
            # Charge and lift
            self.typing_charge = min(self.typing_charge + 1, self.typing_max_charge)
            cf = self.typing_charge / max(1, self.typing_max_charge)
            self.charge_pulses.append({"t": 0.0, "cf": cf})
            self.vcvy = min(self.vcvy, -self.charge_ex_lift)  # velocity floor — consistent lift

        elif self.kb_state == "strike":
            # Extra charge during downswing (no lift, same as regular charge mode)
            self.typing_charge = min(self.typing_charge + 1, self.typing_max_charge)
            cf = self.typing_charge / max(1, self.typing_max_charge)
            self.charge_pulses.append({"t": 0.0, "cf": cf})

        elif self.kb_state == "wait":
            # Pre-input: accept click at any time during wait.
            # The actual lift/arm happens when the state machine exits wait → idle.
            self.charge_prefire = True

    def _update_kb_state_machine(self):
        if not self.kb_active:
            return

        state      = self.kb_state
        near_ready = self.vcy < KB_Y + 60 and abs(self.vcx - KB_X) < 80

        if state == "idle":
            if self.kb_mode == "combo" and self.space_queue > 0 and near_ready:
                self.kb_state = "strike"
            elif self.kb_mode in ("charge", "charge_legacy") and self.typing_wants_strike and near_ready:
                self.typing_wants_strike = False
                self.kb_state = "strike"

        elif state == "strike":
            if self.has_hit or self.vcy >= MAX_VCY:
                if self.kb_mode == "combo":
                    self.space_queue = max(0, self.space_queue - 1)
                if not self.has_hit:
                    self.vcvy = min(self.vcvy, -60.0)
                self.kb_state = "wait"

        elif state == "wait":
            if not self.has_hit and self.vcy < KB_Y + 45:
                if self.kb_mode == "combo":
                    self.kb_state = "strike" if self.space_queue > 0 else "idle"
                elif self.kb_mode == "charge":
                    # lift mode: wait for cooldown, then idle
                    if self.typing_cooldown <= 0:
                        self.kb_state = "idle"
                        if self.charge_prefire:
                            # Pre-input registered — start new cycle immediately
                            self.charge_prefire       = False
                            self.charge_ex_armed      = True
                            self.charge_ex_timer      = self.typing_base_ms
                            self.charge_ex_idle_timer = CHARGE_EX_IDLE_MS
                            self.typing_charge        = 1
                            cf = 1.0 / max(1, self.typing_max_charge)
                            self.charge_pulses.append({"t": 0.0, "cf": cf})
                            self.vcvy = min(self.vcvy, -self.charge_ex_lift)
                else:  # charge_legacy
                    if self.typing_cooldown <= 0:
                        if self.typing_pending:
                            self.typing_pending = False
                            self.kb_state       = "strike"
                        else:
                            self.kb_state = "idle"

    def _kb_target(self) -> tuple[float, float]:
        if self.kb_state == "strike":
            return float(KB_X), float(GAME_H)
        return float(KB_X), float(KB_Y)

    def _on_hit(self, hit_x: float):
        f = int(min(max(self.vel_y, 0), 2000))
        self.last_force   = f
        self.has_hit      = True
        self.hit_cooldown = 380.0
        self.hit_count   += 1

        if self.kb_mode == "charge":
            self.typing_cooldown = 120.0          # short cooldown — just enough for visual
        elif self.kb_mode == "charge_legacy":
            self.typing_cooldown = self.typing_base_ms

        intensity   = f / 2000.0
        charge_mult = 1.0

        if self.kb_mode in ("charge", "charge_legacy"):
            # typing_charge is always ≥1 (trigger click counts as first charge)
            charge_n = max(1, self.typing_charge)
            cf       = charge_n / self.typing_max_charge
            intensity   = min(1.0, intensity + cf * 0.35)
            charge_mult = 1.0 + cf * 3.0
            self.force_count += charge_n

            # ── Turbo mode: track consecutive full-charge hits ─────────────
            if (self.turbo_mode and self.kb_mode in ("charge", "charge_legacy")
                    and not self.fever_active and self.fever_cooldown_timer <= 0):
                if charge_n >= self.typing_max_charge:
                    self.consecutive_full_charge += 1
                    if self.consecutive_full_charge >= self.fever_threshold:
                        self._enter_fever()
                else:
                    self.consecutive_full_charge = 0

            self.typing_charge   = 0
            self.charge_ex_armed = False  # reset EX timer state after each hit
            self.charge_ex_timer = 0.0
        else:
            # Combo mode: each hit = +1 force (no charge system)
            self.force_count += 1

        self.strike_flash = min(0.95,
            (0.06 + intensity * 0.38) * min(2.0, charge_mult * 0.6 + 0.4))
        self.anvil_glow   = min(1.0,
            (0.5  + intensity * 0.5)  * min(1.6, charge_mult * 0.4 + 0.6))

        self.vcvy = -(50 + intensity * 380)
        self.vcvx = 0.0

        cnt = int((10 + intensity * 60) * charge_mult)
        self._emit_sparks(hit_x, FACE_TOP, cnt, intensity)

        return (intensity, charge_mult)

    def _enter_fever(self):
        """Enter Fever state: switch to combo mode for fever_duration seconds."""
        self.fever_active        = True
        self.fever_timer         = self.fever_duration
        self.kb_mode             = "combo"
        self.kb_state            = "idle"
        self.typing_charge       = 0
        self.typing_pending      = False
        self.typing_wants_strike = False
        self.typing_cooldown     = 0.0
        self.charge_prefire      = False
        self.charge_pulses.clear()

    def _exit_fever(self):
        """Exit Fever state: switch back to charge mode, start cooldown."""
        self.fever_active            = False
        self.fever_cooldown_timer    = self.fever_cooldown_duration
        self.consecutive_full_charge = 0
        self.kb_mode                 = "charge"   # return to lift mode after fever
        self.kb_state                = "idle"
        self.kb_active               = False
        self.space_queue             = 0
        self.charge_ex_armed         = False
        self.charge_ex_timer         = 0.0
        self.charge_ex_idle_timer    = 0.0

    def _emit_sparks(self, sx: float, sy: float, count: int, intensity: float):
        for _ in range(count):
            a    = -math.pi + random.random() * math.pi
            spd  = 60 + random.random() * 580 * (0.2 + intensity * 0.8)
            life = 0.22 + random.random() * 0.62
            r    = random.random()
            if   r < 0.25: color = (255, 255, 255)
            elif r < 0.60: color = (255, 221,  34)
            else:          color = (255, 136,   0)
            size = 1.3 + random.random() * 3.8 * intensity
            vx   = math.cos(a) * spd
            vy   = math.sin(a) * spd - 70
            self.sparks.append(Spark(sx, sy, vx, vy, life, size, color))
