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
from game.metal import MetalPiece, pick_metal, METAL_TYPES, SPAWN_DUR, FLASH_DUR
from config import (
    GAME_W, GAME_H,
    AX, AY_BASE, FACE_TOP, FACE_L, FACE_R,
    KB_X, KB_Y, MAX_VCY, APPROACH_DIST,
    IDLE_ANGLE, SWING_ANGLE,
    HEAD_OFFSET, HEAD_THICK, HEAD_PERP,
    HL, HR, HP,
    KX, DX,
    KY, DY,
    TYPING_BASE_MS, TYPING_MAX_CHARGE,
    FEVER_THRESHOLD, FEVER_DURATION, FEVER_COOLDOWN,
    CHARGE_EX_LIFT, CHARGE_EX_IDLE_MS,
    get_charge_color,
)

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
        self.strike_color: tuple = (210, 120, 70)   # RGB of last strike (hammer colour)
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
        self.typing_base_ms:      float = float(_sv.get("typing_base_ms", TYPING_BASE_MS))
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
        self.autostart:       bool = bool(_sv.get("autostart",       False))

        # ── Visual effects (saved) ─────────────────────────────────────────────
        self.show_hit_numbers:   bool = bool(_sv.get("show_hit_numbers",   True))
        self.show_heat_accum:    bool = bool(_sv.get("show_heat_accum",    True))
        self.show_strike_pulse:  bool = bool(_sv.get("show_strike_pulse",  False))  # 預設關閉
        self.show_metal_forge:   bool = bool(_sv.get("show_metal_forge",   True))

        # Hit number popups (transient)
        self.hit_numbers: list = []

        # Heat accumulation (transient): increases on hit, decays slowly
        self.heat_level: float = 0.0

        # Widget position (logical pixels).  None = let Qt decide on first launch.
        _wx = _sv.get("widget_x")
        _wy = _sv.get("widget_y")
        self.widget_x: int | None = int(_wx) if _wx is not None else None
        self.widget_y: int | None = int(_wy) if _wy is not None else None

        # ── Play time ──────────────────────────────────────────────────────
        self.play_time: float = float(_sv.get("play_time", 0.0))

        # ── Turbo / Fever mode (loaded from save) ──────────────────────────
        self.turbo_mode: bool               = bool(_sv.get("turbo_mode", False))
        # Sanitise: turbo mode's base is always "charge"; "combo" only appears
        # during fever.  If we exited mid-fever the save has turbo_mode=True +
        # kb_mode="combo" — correct it so we don't resume in a free-combo state.
        if self.turbo_mode and self.kb_mode == "combo":
            self.kb_mode = "charge"
        self.fever_active: bool             = False
        self.fever_timer: float             = 0.0   # seconds remaining in fever
        self.fever_cooldown_timer: float    = 0.0   # cooldown seconds remaining
        self.consecutive_full_charge: int   = 0
        self.fever_threshold: int           = int(_sv.get("fever_threshold", FEVER_THRESHOLD))
        self.fever_duration: float          = float(_sv.get("fever_duration", FEVER_DURATION))
        self.fever_cooldown_duration: float = float(_sv.get("fever_cooldown_duration", FEVER_COOLDOWN))

        # ── Anvil visibility / style ───────────────────────────────────────
        self.hide_anvil:      bool = bool(_sv.get("hide_anvil",      False))
        self.lock_position:   bool = bool(_sv.get("lock_position",   False))
        self.anvil_v2:        bool = bool(_sv.get("anvil_v2",        True))
        self.always_on_top:   bool = bool(_sv.get("always_on_top",   True))

        # Transient hover state (set by widget, not saved)
        self.mouse_on_widget: bool = False

        # Transient: 連打模式三角點指示器（暫態，不存檔）
        self.combo_dot_idx: int = 0

        # ── Metal forging system ───────────────────────────────────────────
        _fc = _sv.get("forge_counts", [])
        self.forge_counts: list = [int(_fc[i]) if i < len(_fc) else 0
                                   for i in range(len(METAL_TYPES))]
        # 恢復上次未完成的金屬塊（包含進度），否則等第一次敲擊後再生成
        # 金屬鍛造關閉時跳過恢復，直接清空
        _cm_save = _sv.get("current_metal_save") if self.show_metal_forge else None
        if _cm_save is not None:
            try:
                _m = MetalPiece(int(_cm_save["type_idx"]))
                _m.quality  = float(_cm_save.get("quality", 0.0))
                _m.spawn_t  = 1.0    # 視為已完整生成，跳過入場動畫
                _m.complete = bool(_cm_save.get("complete", False))
                self.current_metal:  MetalPiece | None = _m
                self.metal_spawned:  bool              = True
            except Exception:
                self.current_metal:  MetalPiece | None = None
                self.metal_spawned:  bool              = False
        else:
            self.current_metal:  MetalPiece | None = None
            self.metal_spawned:  bool              = (
                bool(_sv.get("metal_spawned", False)) if self.show_metal_forge else False
            )
        # Last hit surface Y — updated each strike, used by renderer for sparks / flash
        self.last_hit_surface_y: float = float(FACE_TOP)

        # ── Critical hit system ────────────────────────────────────────────
        self.crit_rate: float = float(_sv.get("crit_rate", 0.05))   # 0.0–1.0
        self.crit_mult: float = float(_sv.get("crit_mult", 3.0))    # force multiplier
        self.last_crit: bool  = False   # transient: was last hit a crit?

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

        # Heat accumulation slows the glow decay rate
        if self.anvil_glow > 0:
            _glow_decay = 4.0
            if self.show_heat_accum and self.heat_level > 0:
                _glow_decay *= max(0.15, 1.0 - self.heat_level * 0.75)
            self.anvil_glow = max(0.0, self.anvil_glow - dt * _glow_decay)
        if self.heat_level > 0:
            self.heat_level = max(0.0, self.heat_level - dt * 0.20)

        # Hit number popups: advance age and drift upward
        if self.hit_numbers:
            _alive_hn = []
            for _hn in self.hit_numbers:
                _hn["age"] += dt
                _hn["y"]   -= dt * 45.0
                if _hn["age"] < _hn["max_age"]:
                    _alive_hn.append(_hn)
            self.hit_numbers = _alive_hn

        # ── Metal forging animations ──────────────────────────────────────
        if self.current_metal is not None:
            m = self.current_metal
            if m.spawn_t < 1.0:
                m.spawn_t = min(1.0, m.spawn_t + dt / SPAWN_DUR)
            if m.flash_t > 0.0:
                m.flash_t = min(1.0, m.flash_t + dt / FLASH_DUR)
                if m.flash_t >= 1.0:
                    m.dead = True
                    self.current_metal = None
                    if self.show_metal_forge:
                        self._spawn_metal()   # immediately queue next

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
            "kb_mode":                 "charge" if self.turbo_mode else self.kb_mode,
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
            "typing_base_ms":          self.typing_base_ms,
            "charge_ex_lift":          self.charge_ex_lift,
            "widget_x":                self.widget_x,
            "widget_y":                self.widget_y,
            "show_hit_numbers":        self.show_hit_numbers,
            "show_heat_accum":         self.show_heat_accum,
            "show_strike_pulse":       self.show_strike_pulse,
            "show_metal_forge":        self.show_metal_forge,
            "hide_anvil":              self.hide_anvil,
            "lock_position":           self.lock_position,
            "anvil_v2":                self.anvil_v2,
            "always_on_top":           self.always_on_top,
            "forge_counts":            list(self.forge_counts),
            "metal_spawned":           self.metal_spawned,
            "current_metal_save":      self._metal_to_save(),
            "crit_rate":               self.crit_rate,
            "crit_mult":               self.crit_mult,
        }

    def _metal_to_save(self) -> dict | None:
        """將目前金屬塊序列化為可存檔的 dict；不存在或正在閃爍消失則回傳 None。"""
        m = self.current_metal
        if (m is None or m.dead
                or m.spawn_t < 1.0    # 入場動畫未完成
                or m.flash_t > 0.0):  # 完成閃爍動畫中
            return None
        return {
            "type_idx": m.type_idx,
            "quality":  m.quality,
            "complete": m.complete,
        }

    def reset_save(self):
        """Clear all statistics and restore every setting to its default."""
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
        self.typing_base_ms      = TYPING_BASE_MS
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
        self.autostart       = False
        self.widget_x        = None
        self.widget_y        = None
        # Visuals
        self.strike_flash = 0.0
        self.anvil_glow   = 0.0
        self.strike_color = (210, 120, 70)
        self.sparks.clear()
        # Visual effects toggles
        self.show_hit_numbers   = True
        self.show_heat_accum    = True
        self.show_strike_pulse  = False
        self.show_metal_forge   = True
        self.hit_numbers        = []
        self.heat_level         = 0.0
        self.hide_anvil         = False
        self.lock_position      = False
        self.anvil_v2           = True
        self.always_on_top      = True
        self.mouse_on_widget    = False
        # Metal forging
        self.forge_counts        = [0] * len(METAL_TYPES)
        self.metal_spawned       = False
        self.current_metal       = None
        self.last_hit_surface_y  = float(FACE_TOP)
        # Crit
        self.crit_rate  = 0.05
        self.crit_mult  = 3.0
        self.last_crit  = False
        self.combo_dot_idx = 0

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
        # Pre-compute charge for feature 2 popup (before typing_charge is reset below)
        _charge_n_popup = (max(1, self.typing_charge)
                           if self.kb_mode in ("charge", "charge_legacy") else 1)
        # Metal force — same unit as force_count increment; captured before reset
        _metal_force = (_charge_n_popup if self.kb_mode in ("charge", "charge_legacy")
                        else 1)
        # Actual visual hit surface Y: top of metal when visible, else anvil face
        _m = self.current_metal
        if (not self.hide_anvil and _m is not None and not _m.dead
                and _m.spawn_t >= 1.0 and _m.flash_t <= 0.0):
            _hit_y = FACE_TOP - _m.thickness
        else:
            _hit_y = float(FACE_TOP)
        self.last_hit_surface_y = _hit_y

        f = int(min(max(self.vel_y, 0), 2000))
        # Critical hit — multiplies force for quality, intensity, and visuals
        is_crit       = random.random() < self.crit_rate
        self.last_crit = is_crit
        if is_crit:
            f            = int(f * self.crit_mult)
            _metal_force = int(_metal_force * self.crit_mult)
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
            if (self.turbo_mode
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
            self.strike_color    = get_charge_color(cf)
        else:
            # Combo mode: each hit = +1 force (no charge system)
            self.force_count  += 1
            self.strike_color  = (210, 120, 70)   # default amber

        self.strike_flash = min(0.95,
            (0.06 + intensity * 0.38) * min(2.0, charge_mult * 0.6 + 0.4))
        self.anvil_glow   = min(1.0,
            (0.5  + intensity * 0.5)  * min(1.6, charge_mult * 0.4 + 0.6))
        # Floating hit number popup
        if self.show_hit_numbers:
            self.hit_numbers.append({
                "value":   _metal_force,
                "x":       hit_x,
                "y":       _hit_y - 8,
                "age":     0.0,
                "max_age": 0.80 if not is_crit else 1.10,
                "color":   self.strike_color,
                "crit":    is_crit,
            })

        # Heat accumulation — raise heat level on each hit
        if self.show_heat_accum:
            self.heat_level = min(1.0, self.heat_level + 0.20)

        self.vcvy = -(50 + intensity * 380)
        self.vcvx = 0.0

        cnt = int((10 + intensity * 60) * charge_mult)
        self._emit_sparks(hit_x, _hit_y, cnt, intensity)

        # ── Metal forging logic ────────────────────────────────────────────
        if self.show_metal_forge:
            if self.current_metal is not None:
                m = self.current_metal
                if m.complete:
                    # This strike triggers flash — count only once (flash_t guard)
                    if m.flash_t <= 0.0:
                        m.flash_t = 0.001
                        self.forge_counts[m.type_idx] += 1
                elif m.spawn_t >= 1.0:
                    # Metal fully spawned — accumulate quality
                    m.add_quality(float(_metal_force))
            elif not self.metal_spawned:
                # Very first strike of this session → spawn first metal
                self.metal_spawned = True
                self._spawn_metal()

        # 連打模式三角點：每次打擊推進一格（非渦輪模式）
        if self.kb_mode == "combo" and not self.turbo_mode:
            self.combo_dot_idx = (self.combo_dot_idx + 1) % 3

        return (intensity, charge_mult)

    def _spawn_metal(self):
        """Spawn a new metal piece on the anvil (weighted random type)."""
        self.current_metal = MetalPiece(pick_metal())

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
