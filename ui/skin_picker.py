"""ui/skin_picker.py — Skin picker dialog.

Reads all skin definitions from game.skin_registry — this file contains
zero drawing logic of its own.  To add a new skin, register it in
game/skin_registry.py; nothing here needs to change.
"""
from __future__ import annotations

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QScrollArea,
    QWidget, QGridLayout, QGroupBox, QLabel, QPushButton,
)
from PyQt5.QtCore  import Qt, QRect
from PyQt5.QtGui   import QPainter, QColor, QBrush, QPen, QFont, QPixmap

from game.skin_registry import SKIN_REGISTRY, SkinDef

# ── Card geometry ──────────────────────────────────────────────────────────────
CARD_W  = 88
CARD_H  = 100
LABEL_H = 18
DRAW_H  = CARD_H - LABEL_H   # 82 px drawing canvas
COLS    = 3

# "Default" skins are always owned — they represent no skin equipped.
_DEFAULT_IDS = {"anvil_default", "hammer_default"}

# ── Skin-ordered slot lists (derived from registry insertion order) ────────────
_ANVIL_IDS  = [sk for sk, sd in SKIN_REGISTRY.items() if sd.slot == "anvil"]
_HAMMER_IDS = [sk for sk, sd in SKIN_REGISTRY.items() if sd.slot == "hammer"]

# ── Thumbnail pixmap cache ────────────────────────────────────────────────────
_thumb_cache: dict[str, QPixmap] = {}


def _make_thumbnail(skin_id: str, w: int, h: int) -> QPixmap:
    """Render a thumbnail for skin_id at size w×h (cached)."""
    key = f"{skin_id}_{w}_{h}"
    if key in _thumb_cache:
        return _thumb_cache[key]

    sd  = SKIN_REGISTRY.get(skin_id)
    pix = QPixmap(w, h)
    pix.fill(Qt.transparent)

    if sd is not None:
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        sd.draw_thumb(p, w, h)
        p.end()

    _thumb_cache[key] = pix
    return pix


# ── SkinCard ───────────────────────────────────────────────────────────────────

class SkinCard(QWidget):
    def __init__(self, skin_id: str, owned: bool, active: bool,
                 on_equip, parent=None):
        super().__init__(parent)
        self.skin_id   = skin_id
        self.owned     = owned
        self.active    = active
        self._on_equip = on_equip
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.PointingHandCursor if owned else Qt.ForbiddenCursor)

        sd = SKIN_REGISTRY.get(skin_id)
        label = sd.label if sd else skin_id
        self._label = label
        self.setToolTip(label if owned else f"{label}（尚未解鎖）")

    def set_active(self, active: bool):
        if self.active != active:
            self.active = active
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.owned:
            self._on_equip(self.skin_id)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # ── Background & border ───────────────────────────────────────────────
        if self.active:
            p.setBrush(QBrush(QColor(255, 215, 55, 55)))
            p.setPen(QPen(QColor(220, 178, 20), 2.5))
        elif not self.owned:
            p.setBrush(QBrush(QColor(36, 36, 38)))
            p.setPen(QPen(QColor(65, 65, 68), 1.5))
        else:
            p.setBrush(QBrush(QColor(50, 50, 54)))
            p.setPen(QPen(QColor(88, 88, 92), 1.5))
        p.drawRoundedRect(1, 1, CARD_W - 2, CARD_H - 2, 8, 8)

        # ── Thumbnail (centred in drawing canvas) ─────────────────────────────
        thumb = _make_thumbnail(self.skin_id, CARD_W - 10, DRAW_H - 8)
        tx = (CARD_W - thumb.width())  // 2
        ty = 4 + max(0, (DRAW_H - 8 - thumb.height()) // 2)
        p.drawPixmap(tx, ty, thumb)

        # ── Locked overlay ────────────────────────────────────────────────────
        if not self.owned:
            p.setBrush(QBrush(QColor(0, 0, 0, 155)))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(1, 1, CARD_W - 2, CARD_H - 2, 8, 8)
            pen = QPen(QColor(220, 48, 48), 3.5,
                       Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            cx, cy, r = CARD_W // 2, DRAW_H // 2, 15
            p.drawLine(cx - r, cy - r, cx + r, cy + r)
            p.drawLine(cx + r, cy - r, cx - r, cy + r)

        # ── Active checkmark badge ────────────────────────────────────────────
        if self.active:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(220, 178, 20)))
            p.drawEllipse(CARD_W - 20, 4, 16, 16)
            p.setPen(QPen(QColor(30, 22, 5), 2.2,
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(CARD_W - 17, 12, CARD_W - 13, 16)
            p.drawLine(CARD_W - 13, 16, CARD_W -  7,  8)

        # ── Name label ────────────────────────────────────────────────────────
        p.setPen(QColor(212, 212, 212) if self.owned else QColor(100, 100, 100))
        f = QFont(); f.setPixelSize(11); p.setFont(f)
        p.drawText(QRect(0, CARD_H - LABEL_H, CARD_W, LABEL_H),
                   Qt.AlignCenter, self._label)


# ── SkinPickerDialog ───────────────────────────────────────────────────────────

class SkinPickerDialog(QDialog):
    _STYLE = """
        QDialog  { background: #252528; }
        QGroupBox {
            color: #c8c8c8; font-weight: bold;
            border: 1px solid #454548; border-radius: 7px;
            margin-top: 10px; padding-top: 4px;
        }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        QScrollArea { border: none; background: transparent; }
        QScrollBar:vertical {
            background: #2e2e32; width: 8px; border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background: #606068; border-radius: 4px; min-height: 20px;
        }
        QPushButton {
            background: #464648; color: #d8d8d8;
            border: 1px solid #686870; border-radius: 5px;
            padding: 5px 22px; font-size: 12px;
        }
        QPushButton:hover   { background: #565658; }
        QPushButton:pressed { background: #383838; }
    """

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state          = state
        self._anvil_cards:  list[SkinCard] = []
        self._hammer_cards: list[SkinCard] = []
        self.setWindowTitle("造型選擇")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(self._STYLE)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        title = QLabel("✨  造型選擇")
        title.setStyleSheet(
            "color: #e4e4e4; font-size: 14px; font-weight: bold; padding-bottom: 2px;")
        root.addWidget(title)

        anvil_active  = self.state.active_anvil_skin  or "anvil_default"
        hammer_active = self.state.active_hammer_skin or "hammer_default"

        anvil_box, self._anvil_cards = self._make_section(
            "🪨  鐵砧造型", _ANVIL_IDS, anvil_active, self._equip_anvil)
        root.addWidget(anvil_box)

        hammer_box, self._hammer_cards = self._make_section(
            "🔨  錘子造型", _HAMMER_IDS, hammer_active, self._equip_hammer)
        root.addWidget(hammer_box)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _make_section(self, title, skin_ids, active_skin, equip_fn):
        box = QGroupBox(title)
        vl  = QVBoxLayout(box)
        vl.setContentsMargins(6, 6, 6, 8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        rows = max(1, (len(skin_ids) + COLS - 1) // COLS)
        scroll.setFixedHeight(min(rows, 2) * (CARD_H + 6) + 10)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        grid  = QGridLayout(inner)
        grid.setSpacing(6)
        grid.setContentsMargins(4, 4, 4, 4)

        cards: list[SkinCard] = []
        for idx, sk in enumerate(skin_ids):
            owned  = (sk in _DEFAULT_IDS) or (sk in self.state.owned_skins)
            active = (sk == active_skin)
            card   = SkinCard(sk, owned, active, on_equip=equip_fn, parent=inner)
            grid.addWidget(card, idx // COLS, idx % COLS)
            cards.append(card)

        scroll.setWidget(inner)
        vl.addWidget(scroll)
        return box, cards

    def _equip_anvil(self, skin_id: str):
        new = None if skin_id == "anvil_default" else skin_id
        self.state.active_anvil_skin = new
        active_card = skin_id  # "anvil_default" stays selected if new is None
        for card in self._anvil_cards:
            card.set_active(card.skin_id == active_card)
        if self.parent():
            self.parent().update()

    def _equip_hammer(self, skin_id: str):
        new = None if skin_id == "hammer_default" else skin_id
        self.state.active_hammer_skin = new
        active_card = skin_id
        for card in self._hammer_cards:
            card.set_active(card.skin_id == active_card)
        if self.parent():
            self.parent().update()
