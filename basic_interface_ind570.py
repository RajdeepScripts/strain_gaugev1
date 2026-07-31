#!/usr/bin/env python3
"""
basic_interface_ind570.py — 4x4 Basic Interface (IND570 over TCP)
Replicates the Russian 4-channel layout exactly. Data from Mettler-Toledo IND570.
"""

import re
import socket
import sys
import threading
from collections import deque

from PyQt5.QtCore import Qt, QTimer, QRect
from PyQt5.QtGui import QFont, QPainter, QColor, QPen
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

# ── Config ────────────────────────────────────────────────────────────────────
IND570_HOST = "192.168.0.1"
IND570_PORT = 1702
AVG_MAX     = 1000
KG_TO_KN    = 0.00981

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
            self.result_kg = avg - self._tare_kg
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
        self._cal_offset = 0.0
        self._zeroed     = False
        self._base_val   = None
        self._last_val   = 0.0
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

    def _on_zero(self):
        self._zeroed   = True
        self._base_val = None

    def _on_cancel_zero(self):
        self._zeroed = False

    def set_delta(self, val: float, raw_kg: float):
        self._base_val = val
        self._base_kg  = raw_kg

    def clear_delta(self):
        self._base_val = None
        self._base_kg  = None

    def update_value(self, val: float, unit: str, raw_kg: float):
        self._last_val = val

        self._unit_label.setText(unit)

        if self._zeroed:
            self._top_display.setText(" 0")
            self._kn_display.setText(" 0")
            return

        self._top_display.setText("0")

        if self._base_val is not None:
            # delta in raw kg divided by 1000, added to mode-converted base
            display_val = self._base_val + (raw_kg - self._base_kg) / 1000.0
        else:
            display_val = val

        self._kn_display.setText(f"{display_val + self._cal_offset:.3f}")


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("4x4 Basic Interface")
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

        btn_delta = QPushButton("Δ  Delta")
        btn_live  = QPushButton("Live")
        btn_delta.clicked.connect(self._on_delta)
        btn_live.clicked.connect(self._on_live)
        bar.addWidget(btn_delta)
        bar.addWidget(btn_live)

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
        val, unit = self._apply_mode(kg, mode)
        for ch in self._channels:
            ch.set_delta(val, kg)
        self._base_label.setText(f"Base: {val:.3f} {unit}")

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
                ch.update_value(val, unit, kg)

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
