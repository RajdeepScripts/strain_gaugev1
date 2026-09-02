#!/usr/bin/env python3
"""
basic_interface_ind570.py — 4x4 Basic Interface (IND570 over TCP)
4-channel weight display. Data from Mettler-Toledo IND570.
"""

import re
import socket
import sys
import threading
from collections import deque

from PyQt5.QtCore import Qt, QTimer, QRect
from PyQt5.QtGui import QFont, QPainter, QColor, QPen
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDialog, QDoubleSpinBox, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

# ── Config ────────────────────────────────────────────────────────────────────
# SOFTWARE_ID: increment whenever a change could affect metrological function
# or accuracy (calculations, parsing, averaging, calibration/zero handling).
# Purely cosmetic UI changes do not require a bump.
SOFTWARE_ID = "1.0.0"

IND570_HOST = "192.168.0.1"
IND570_PORT = 1702
AVG_MAX     = 1000
KG_TO_KN    = 0.00981

# ── Metrological constants (OIML R 106-1 §3.2.7) ────────────────────────────
# Scale interval, d, and maximum capacity, Max, as configured on the IND570.
SCALE_INTERVAL_KG = 0.01    # d  = 10 g
MAX_CAPACITY_KG   = 150.0   # Max = 150 kg

ZERO_ACCURACY_KG       = 0.25 * SCALE_INTERVAL_KG   # §3.2.7.1: ±0.25 d
ZERO_RANGE_KG           = 0.04 * MAX_CAPACITY_KG      # §3.2.7.2: 4% of Max
INITIAL_ZERO_RANGE_KG   = 0.20 * MAX_CAPACITY_KG      # §3.2.7.2: 20% of Max (initial)

# §3.3.5.3 "stable equilibrium": indication must not deviate by more than
# 1 scale interval across the samples checked before zero-setting is allowed.
STABILITY_WINDOW_SAMPLES = 5
STABILITY_BAND_KG        = 1.0 * SCALE_INTERVAL_KG

# Default per-channel calibration offset (kg), applied automatically on
# startup so it doesn't need to be re-entered every session. Derived from
# comparing a known reference weight against the IND570's raw TCP stream
# (stream read +0.03 kg high). Update this if that discrepancy changes.
DEFAULT_CAL_OFFSET_KG = -0.03

_LINE_RE = re.compile(r'([+-]?\d+\.?\d*)\s*(kg|g|lb|t)', re.IGNORECASE)


def _parse_line(line: str):
    m = _LINE_RE.search(line)
    if not m:
        return None
    value = float(m.group(1))
    unit  = m.group(2).lower()
    if unit == 'g':
        value /= 1000.0
    elif unit == 'lb':
        value *= 0.453592
    elif unit == 't':
        value *= 1000.0
    return value


# ── Shared state ──────────────────────────────────────────────────────────────

class SharedState:
    def __init__(self, avg_win=100):
        self._lock    = threading.Lock()
        self.valid    = 0
        self.result_kg = 0.0
        self.result_kn = 0.0
        self.bytes_rx  = 0
        self._buf     = deque(maxlen=max(1, avg_win))
        self._tare_kg = 0.0

    def set_avg_window(self, n):
        with self._lock:
            self._buf = deque(maxlen=max(1, min(n, AVG_MAX)))

    def push(self, kg, bytes_rx):
        with self._lock:
            self._buf.append(kg)
            avg = sum(self._buf) / len(self._buf)
            # DEFAULT_CAL_OFFSET_KG is applied here so every consumer of
            # result_kg (channel displays, the OIML error-test wizard, Delta)
            # sees the same, already-calibrated value — not just the display.
            self.result_kg = avg - self._tare_kg + DEFAULT_CAL_OFFSET_KG
            self.result_kn = self.result_kg * KG_TO_KN
            self.bytes_rx  = bytes_rx
            self.valid     = 1

    def set_error(self):
        with self._lock:
            self.valid = -1

    def snapshot(self):
        with self._lock:
            return {
                'valid':     self.valid,
                'result_kg': self.result_kg,
                'result_kn': self.result_kn,
                'bytes_rx':  self.bytes_rx,
            }


# ── TCP reader thread ─────────────────────────────────────────────────────────

def reader_thread(state, running):
    while running.is_set():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect((IND570_HOST, IND570_PORT))
            buf      = ''
            bytes_rx = 0
            while running.is_set():
                chunk = sock.recv(256).decode('ascii', errors='ignore')
                if not chunk:
                    break
                bytes_rx += len(chunk)
                buf += chunk
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    kg = _parse_line(line)
                    if kg is not None:
                        state.push(kg, bytes_rx)
        except Exception:
            state.set_error()
        finally:
            try:
                sock.close()
            except Exception:
                pass
        if running.is_set():
            threading.Event().wait(2)


# ── 7-Segment display widget ──────────────────────────────────────────────────
#
# Each digit is drawn as 7 segments (a-g):
#
#   aaa
#  f   b
#  f   b
#   ggg
#  e   c
#  e   c
#   ddd
#
# Segments per digit:
_SEG_MAP = {
    '0': 'abcdef',
    '1': 'bc',
    '2': 'abdeg',
    '3': 'abcdg',
    '4': 'bcfg',
    '5': 'acdfg',
    '6': 'acdefg',
    '7': 'abc',
    '8': 'abcdefg',
    '9': 'abcdfg',
    '-': 'g',
    '.': '.',
    ' ': '',
}

class SegmentDisplay(QWidget):
    def __init__(self, parent=None, height=80):
        super().__init__(parent)
        self._text = "0"
        self._seg_color  = QColor(30, 30, 30)
        self._off_color  = QColor(210, 210, 210)
        self._bg_color   = QColor(255, 255, 255)
        self.setFixedHeight(height)
        self.setMinimumWidth(60)

    def setText(self, text: str):
        self._text = text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # background
        p.fillRect(self.rect(), self._bg_color)

        # border
        pen = QPen(QColor(160, 160, 160))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawRect(1, 1, self.width()-2, self.height()-2)

        chars = list(self._text)
        digit_chars = [c for c in chars if c != '.']
        n = max(len(digit_chars), 1)
        dot_count = chars.count('.')

        h = self.height() - 12
        sw = 3          # segment thickness
        gap = 4         # gap between digits
        dot_w = sw + 6  # space reserved for each dot
        dw = min(int((self.width() - 12 - dot_count * dot_w) / n) - gap, int(h * 0.6))
        dh = h

        # total drawn width including dot slots
        total_w = n * (dw + gap) - gap + dot_count * dot_w
        x0 = (self.width() - total_w) // 2
        y0 = 6

        di = 0   # digit index
        cx = x0  # current x cursor
        for ch in chars:
            if ch == '.':
                px = cx
                py = y0 + dh - sw - 2
                p.setBrush(self._seg_color)
                p.setPen(Qt.NoPen)
                p.drawEllipse(px, py, sw + 3, sw + 3)
                cx += dot_w
                continue

            segs = _SEG_MAP.get(ch, '')
            self._draw_digit(p, cx, y0, dw, dh, sw, segs)
            cx += dw + gap
            di += 1

        p.end()

    def _draw_digit(self, p, x, y, w, h, sw, segs):
        mh = h // 2

        def seg(name, on):
            color = self._seg_color if on else self._off_color
            p.setBrush(QColor(color))
            p.setPen(Qt.NoPen)
            if name == 'a':   # top horizontal
                p.drawRect(x+sw, y, w-2*sw, sw)
            elif name == 'b': # top-right vertical
                p.drawRect(x+w-sw, y+sw, sw, mh-2*sw)
            elif name == 'c': # bottom-right vertical
                p.drawRect(x+w-sw, y+mh+sw, sw, mh-2*sw)
            elif name == 'd': # bottom horizontal
                p.drawRect(x+sw, y+h-sw, w-2*sw, sw)
            elif name == 'e': # bottom-left vertical
                p.drawRect(x, y+mh+sw, sw, mh-2*sw)
            elif name == 'f': # top-left vertical
                p.drawRect(x, y+sw, sw, mh-2*sw)
            elif name == 'g': # middle horizontal
                p.drawRect(x+sw, y+mh-sw//2, w-2*sw, sw)

        for s in segs:
            seg(s, True)


# ── Channel column ────────────────────────────────────────────────────────────

class ChannelColumn(QWidget):
    def __init__(self, ch_num, parent=None):
        super().__init__(parent)
        # Global correction (DEFAULT_CAL_OFFSET_KG) is already applied in
        # SharedState.push(), so this per-channel offset starts at 0 and is
        # only for additional, channel-specific fine-tuning if ever needed.
        self._cal_offset  = 0.0
        self._base_val    = None
        self._last_val    = 0.0
        self._zero_offset = 0.0
        self._zero_set    = False
        self._first_zero  = True   # first zero-set uses the wider "initial" range
        self._recent_kg   = deque(maxlen=STABILITY_WINDOW_SAMPLES)
        self._build(ch_num)

    def _build(self, ch_num):
        col = QVBoxLayout(self)
        col.setSpacing(4)
        col.setContentsMargins(4, 4, 4, 4)

        # Channel title
        title = QLabel(f"Chanel {ch_num}")
        title.setFont(QFont("Arial", 9, QFont.Bold))
        col.addWidget(title)

        # Top large display (raw value / q)
        self._top_display = SegmentDisplay(height=80)
        col.addWidget(self._top_display)

        # temp =
        temp_row = QHBoxLayout()
        temp_row.addWidget(QLabel("temp ="))
        self._temp_edit = QLineEdit("0")
        self._temp_edit.setReadOnly(True)
        self._temp_edit.setFixedWidth(55)
        temp_row.addWidget(self._temp_edit)
        temp_row.addStretch()
        col.addLayout(temp_row)

        # two k = rows
        self._k_edits = []
        for _ in range(2):
            k_row = QHBoxLayout()
            k_row.addWidget(QLabel("k ="))
            k_edit = QLineEdit("0.0067563")
            k_edit.setMinimumWidth(80)
            k_row.addWidget(k_edit)
            col.addLayout(k_row)
            self._k_edits.append(k_edit)

        # value display box + unit label side by side
        kn_row = QHBoxLayout()
        self._kn_display = SegmentDisplay(height=80)
        kn_row.addWidget(self._kn_display)
        self._unit_label = QLabel("kg")
        self._unit_label.setFont(QFont("Arial", 9))
        self._unit_label.setFixedWidth(30)
        kn_row.addWidget(self._unit_label)
        col.addLayout(kn_row)

        # Calibration value label + input
        col.addWidget(QLabel("Calibration value:"))
        self._cal_edit = QLineEdit("0")
        col.addWidget(self._cal_edit)

        # Zero-setting status (§3.2.7 accept/reject feedback)
        self._zero_status = QLabel("")
        self._zero_status.setStyleSheet("color: #a00; font-size: 10px;")
        self._zero_status.setWordWrap(True)
        col.addWidget(self._zero_status)

        # Buttons — full width, stacked
        btn_cal    = QPushButton("Calibrate")
        btn_zero   = QPushButton("Zero")
        btn_cancel = QPushButton("Cancel zero")
        btn_cal.clicked.connect(self._on_calibrate)
        btn_zero.clicked.connect(self._on_zero)
        btn_cancel.clicked.connect(self._on_cancel_zero)
        col.addWidget(btn_cal)
        col.addWidget(btn_zero)
        col.addWidget(btn_cancel)

        col.addStretch()

    def _on_calibrate(self):
        try:
            self._cal_offset = float(self._cal_edit.text())
        except ValueError:
            self._cal_offset = 0.0

    def _is_stable(self):
        # §3.3.5.3 stable equilibrium: indication must not vary by more than
        # 1 scale interval across the recent sample window.
        if len(self._recent_kg) < STABILITY_WINDOW_SAMPLES:
            return False
        return (max(self._recent_kg) - min(self._recent_kg)) <= STABILITY_BAND_KG

    def _on_zero(self):
        # §3.2.7.3(a): semi-automatic zero-setting shall function only when
        # the instrument is in stable equilibrium.
        if not self._is_stable():
            self._zero_status.setText("Zero rejected: reading not stable")
            return

        candidate = self._recent_kg[-1] if self._recent_kg else 0.0
        allowed_range = INITIAL_ZERO_RANGE_KG if self._first_zero else ZERO_RANGE_KG

        # §3.2.7.2: zero-setting range capped at 4% of Max (20% for the
        # initial zero-setting).
        if abs(candidate) > allowed_range:
            self._zero_status.setText(
                f"Zero rejected: {candidate:+.3f} kg exceeds "
                f"±{allowed_range:.2f} kg zero-setting range"
            )
            return

        self._zero_offset = candidate
        self._zero_set    = True
        self._first_zero  = False
        self._base_val    = None
        self._zero_status.setText(
            f"Zeroed (offset {self._zero_offset:+.3f} kg, "
            f"accuracy target ±{ZERO_ACCURACY_KG:.3f} kg)"
        )

    def _on_cancel_zero(self):
        self._zero_offset = 0.0
        self._zero_set    = False
        self._zero_status.setText("")

    def set_delta(self, val: float, raw_kg: float):
        # val is this channel's calibrated, zero-compensated, mode-scaled
        # base (see MainWindow._on_delta); raw_kg is the uncalibrated raw
        # kg reading at press-time, used by update_value() to recompute
        # the same base on the fly as zero/calibration may change later.
        self._base_val = val
        self._base_kg  = raw_kg

    def clear_delta(self):
        self._base_val = None
        self._base_kg  = None

    def update_value(self, val: float, unit: str, raw_kg: float,
                      mode: str = None, apply_mode_fn=None):
        self._last_val = val
        self._recent_kg.append(raw_kg)

        self._unit_label.setText(unit)
        self._top_display.setText("0")

        # Calibration is applied to the raw kg reading, before mode scaling,
        # so a correction like -0.03 kg scales consistently across kg /
        # 2×kg / Tons / 2×Tons instead of being a flat, mode-blind offset.
        cal_raw_kg = raw_kg - self._zero_offset + self._cal_offset

        if apply_mode_fn is not None and mode is not None:
            display_candidate, _ = apply_mode_fn(cal_raw_kg, mode)
        else:
            display_candidate = val

        if self._base_val is not None:
            # Delta = current calibrated value minus the base calibrated
            # value, both run through the same mode conversion, so units,
            # scale, and calibration all match at press-time and after.
            if apply_mode_fn is not None and mode is not None:
                base_cal_raw_kg = self._base_kg - self._zero_offset + self._cal_offset
                base_val_now, _ = apply_mode_fn(base_cal_raw_kg, mode)
            else:
                base_val_now = self._base_val
            display_val = self._base_val + (display_candidate - base_val_now) / 1000.0
        else:
            display_val = display_candidate

        self._kn_display.setText(f"{display_val:.3f}")


# ── OIML R 106-1 §A.3.5 error-prior-to-rounding wizard ────────────────────────
#
# Guides a single ΔL pass (either the zero-load pass for E0, or the loaded
# pass for E), per §A.3.5.1:
#   P = I + 0.5*d - ΔL
#   E = P - L
# The MainWindow ties two passes together and computes Ec = E - E0.

class ErrorPassDialog(QDialog):
    """One pass of the §A.3.5.1 procedure: capture I, watch for a 1-d tick,
    ask for the total added weight (ΔL), then report P and E for this pass."""

    def __init__(self, state: 'SharedState', reference_load_kg: float,
                 pass_label: str, parent=None):
        super().__init__(parent)
        self._state = state
        self._L     = reference_load_kg
        self.result_E = None
        self.result_P = None
        self.result_I = None

        self.setWindowTitle(f"§A.3.5.1 Error Test — {pass_label}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            f"Reference load L = {self._L:.3f} t\n"
            f"Scale interval d = {SCALE_INTERVAL_KG:.3f} kg\n\n"
            f"Step 1: leave the load undisturbed and press \"Capture I\" to "
            f"record the current indication.\nStep 2: add small test "
            f"weights (~{0.1 * SCALE_INTERVAL_KG:.4f} kg each) until "
            f"the indication increases by one full scale interval, then "
            f"press \"Indication ticked up\"."
        ))

        self._i_label = QLabel("I = (not captured)")
        layout.addWidget(self._i_label)

        self._live_label = QLabel("Live: —")
        layout.addWidget(self._live_label)

        self._btn_capture = QPushButton("Capture I")
        self._btn_capture.clicked.connect(self._on_capture_i)
        layout.addWidget(self._btn_capture)

        self._btn_ticked = QPushButton("Indication ticked up by 1 d")
        self._btn_ticked.setEnabled(False)
        self._btn_ticked.clicked.connect(self._on_ticked)
        layout.addWidget(self._btn_ticked)

        self._result_label = QLabel("")
        self._result_label.setWordWrap(True)
        layout.addWidget(self._result_label)

        self._btn_close = QPushButton("Close")
        self._btn_close.setEnabled(False)
        self._btn_close.clicked.connect(self.accept)
        layout.addWidget(self._btn_close)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_live)
        self._timer.start(200)

    def _refresh_live(self):
        d = self._state.snapshot()
        if d['valid'] == 1:
            self._live_label.setText(f"Live: {d['result_kg']:.3f} t")

    def _on_capture_i(self):
        d = self._state.snapshot()
        if d['valid'] != 1:
            QMessageBox.warning(self, "No data",
                                 "No valid live reading yet — check the "
                                 "IND570 connection and try again.")
            return
        self.result_I = d['result_kg']
        self._i_label.setText(f"I = {self.result_I:.3f} t")
        self._btn_capture.setEnabled(False)
        self._btn_ticked.setEnabled(True)

    def _on_ticked(self):
        if self.result_I is None:
            return
        delta_l, ok = self._prompt_delta_l()
        if not ok:
            return

        # §A.3.5.1: P = I + 0.5*d - ΔL ; E = P - L
        self.result_P = self.result_I + 0.5 * SCALE_INTERVAL_KG - delta_l
        self.result_E = self.result_P - self._L

        self._result_label.setText(
            f"ΔL = {delta_l:.4f} t\n"
            f"P (indication prior to rounding) = {self.result_P:.4f} t\n"
            f"E (error) = {self.result_E:.4f} t"
        )
        self._btn_ticked.setEnabled(False)
        self._btn_close.setEnabled(True)

    def _prompt_delta_l(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Total added weight (ΔL)")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(
            "Enter the total weight added since \"Capture I\" that caused "
            "the indication to tick up by one scale interval (kg):"
        ))
        spin = QDoubleSpinBox()
        spin.setDecimals(4)
        spin.setRange(0.0, MAX_CAPACITY_KG)
        spin.setSingleStep(0.001)
        v.addWidget(spin)
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(dlg.accept)
        v.addWidget(btn_ok)
        accepted = dlg.exec_() == QDialog.Accepted
        return spin.value(), accepted


class ErrorTestSummaryDialog(QDialog):
    """Runs both passes (zero load, then test load) and reports Ec."""

    def __init__(self, state: 'SharedState', parent=None):
        super().__init__(parent)
        self._state = state
        self.setWindowTitle("OIML R 106-1 §A.3.5.1 — Error Prior to Rounding")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "This runs the §A.3.5.1 procedure at a reference load L you "
            "choose (use L=0 for a zero-load check): E = P - L."
        ))

        row = QHBoxLayout()
        row.addWidget(QLabel("Reference load L (kg):"))
        self._l_spin = QDoubleSpinBox()
        self._l_spin.setDecimals(3)
        self._l_spin.setRange(0.0, MAX_CAPACITY_KG)
        self._l_spin.setSingleStep(0.1)
        row.addWidget(self._l_spin)
        layout.addLayout(row)

        self._btn_run = QPushButton("Run test")
        self._btn_run.clicked.connect(self._run_test)
        layout.addWidget(self._btn_run)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.accept)
        layout.addWidget(self._btn_close)

    def _run_test(self):
        # L is entered as a raw kg-scale number, same scale as I and ΔL —
        # only the *displayed* text is multiplied by 1000 and labeled "t".
        L = self._l_spin.value()
        dlg = ErrorPassDialog(self._state, L, f"L={L:.3f} t", self)
        dlg.exec_()
        if dlg.result_E is not None:
            self._log.append(
                f"[L={L:.4f} t] I={dlg.result_I:.4f} t, "
                f"P={dlg.result_P:.4f} t, E={dlg.result_E:.4f} t"
            )


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"4x4 Basic Interface — SW {SOFTWARE_ID}")
        self.showMaximized()

        self._state   = SharedState(avg_win=100)
        self._running = threading.Event()
        self._running.set()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        self._build_top_bar(root)
        self._build_channel_data_label(root)
        self._build_channels(root)
        self._build_bottom_bar(root)

        self._start_reader()

        self._timer = QTimer()
        self._timer.timeout.connect(self._update_ui)
        self._timer.start(100)

    # ── Top bar ───────────────────────────────────────────────────────────────

    def _build_top_bar(self, root):
        com_group = QGroupBox("COM-Port controls")
        bar = QHBoxLayout(com_group)
        bar.setSpacing(6)

        self._btn_start = QPushButton("Start")
        self._btn_stop  = QPushButton("Stop")
        self._btn_stop.setEnabled(False)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)

        self._com_combo = QComboBox()
        self._com_combo.addItems(["COM7", f"{IND570_HOST}:{IND570_PORT}"])
        self._com_combo.setCurrentIndex(1)

        bar.addWidget(self._btn_start)
        bar.addWidget(self._btn_stop)
        bar.addWidget(self._com_combo)
        bar.addWidget(QPushButton("Update port list"))
        bar.addWidget(QPushButton("Save port name"))
        bar.addStretch()

        outer = QHBoxLayout()
        outer.addWidget(com_group)
        outer.addStretch()
        outer.addWidget(QPushButton("Save coefficients"))
        root.addLayout(outer)

    def _build_channel_data_label(self, root):
        lbl = QLabel("Chanel data")
        lbl.setFont(QFont("Arial", 9, QFont.Bold))
        root.addWidget(lbl)

    # ── 4 channel columns side by side ───────────────────────────────────────

    def _build_channels(self, root):
        self._channels = []
        row = QHBoxLayout()
        row.setSpacing(4)
        for i in range(4):
            ch = ChannelColumn(i + 1)
            self._channels.append(ch)
            row.addWidget(ch)
            if i < 3:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setFrameShadow(QFrame.Sunken)
                row.addWidget(sep)
        root.addLayout(row, stretch=1)

    # ── Bottom bar ────────────────────────────────────────────────────────────

    def _build_bottom_bar(self, root):
        bar = QHBoxLayout()

        bar.addWidget(QLabel("Average by"))
        self._avg_spin = QSpinBox()
        self._avg_spin.setRange(1, AVG_MAX)
        self._avg_spin.setValue(100)
        self._avg_spin.valueChanged.connect(lambda v: self._state.set_avg_window(v))
        bar.addWidget(self._avg_spin)
        bar.addWidget(QLabel("measurements"))

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        bar.addWidget(sep)

        bar.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["kg", "2 × kg", "Tons", "2 × Tons"])
        self._mode_combo.setFixedWidth(110)
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        bar.addWidget(self._mode_combo)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        bar.addWidget(sep2)

        self._btn_delta = QPushButton("Δ  Delta")
        self._btn_live  = QPushButton("Live")
        self._btn_delta.clicked.connect(self._on_delta)
        self._btn_live.clicked.connect(self._on_live)
        bar.addWidget(self._btn_delta)
        bar.addWidget(self._btn_live)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.VLine)
        bar.addWidget(sep3)

        self._base_label = QLabel("")
        self._base_label.setStyleSheet("color: #0055aa; font-weight: bold;")
        bar.addWidget(self._base_label)

        sep4 = QFrame()
        sep4.setFrameShape(QFrame.VLine)
        bar.addWidget(sep4)

        self._status_label = QLabel("Last packages: —   Bytes on port: 0")
        bar.addWidget(self._status_label, stretch=1)

        btn_error_test = QPushButton("OIML §A.3.5 Error Test")
        btn_error_test.clicked.connect(self._on_open_error_test)
        bar.addWidget(btn_error_test)

        btn_exit = QPushButton("Exit program")
        btn_exit.clicked.connect(self.close)
        bar.addWidget(btn_exit)

        root.addLayout(bar)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_start(self):
        if not self._running.is_set():
            self._running.set()
            self._start_reader()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)

    def _on_stop(self):
        self._running.clear()
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)

    def _on_mode_changed(self, mode: str):
        unit = "T" if "Tons" in mode else "kg"
        for ch in self._channels:
            ch._unit_label.setText(unit)

    def _apply_mode(self, kg: float, mode: str):
        if mode == "kg":
            return kg, "kg"
        elif mode == "2 × kg":
            return kg * 2, "kg"
        elif mode == "Tons":
            return kg, "T"
        else:
            return kg * 2, "T"

    def _on_delta(self):
        d = self._state.snapshot()
        if d['valid'] != 1:
            return
        kg   = d['result_kg']
        mode = self._mode_combo.currentText()
        _, unit = self._apply_mode(kg, mode)
        for ch in self._channels:
            # Base is each channel's own calibrated, mode-scaled value —
            # calibration applied to raw kg before mode scaling, matching
            # update_value(), so it stays consistent across kg/2×kg/Tons/
            # 2×Tons and doesn't drift when Delta is pressed.
            base_cal_raw_kg = kg - ch._zero_offset + ch._cal_offset
            base_val, _ = self._apply_mode(base_cal_raw_kg, mode)
            ch.set_delta(base_val, kg)
        # Show the base as it will actually be displayed, using Channel 1's
        # calibration/zero — channels normally share the same settings.
        ch0 = self._channels[0]
        displayed_base_kg = kg - ch0._zero_offset + ch0._cal_offset
        displayed_base, _ = self._apply_mode(displayed_base_kg, mode)
        self._base_label.setText(f"Base: {displayed_base:.3f} {unit}")

    def _on_live(self):
        for ch in self._channels:
            ch.clear_delta()
        self._base_label.setText("")

    def _start_reader(self):
        threading.Thread(
            target=reader_thread,
            args=(self._state, self._running),
            daemon=True
        ).start()

    # ── UI update 100ms ───────────────────────────────────────────────────────

    def _update_ui(self):
        d = self._state.snapshot()

        if d['valid'] == -1:
            self._status_label.setText(
                f"ERROR: Cannot connect to {IND570_HOST}:{IND570_PORT}"
            )
            self._status_label.setStyleSheet("color: red;")
            return

        if d['valid'] == 1:
            self._status_label.setStyleSheet("")
            self._status_label.setText(
                f"Last packages: —   Bytes on port: {d['bytes_rx']}"
            )
            kg   = d['result_kg']
            mode = self._mode_combo.currentText()
            val, unit = self._apply_mode(kg, mode)
            for ch in self._channels:
                ch.update_value(val, unit, kg, mode, self._apply_mode)

    def _on_open_error_test(self):
        dlg = ErrorTestSummaryDialog(self._state, self)
        dlg.exec_()

    def closeEvent(self, event):
        self._running.clear()
        event.accept()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
