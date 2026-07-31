#!/usr/bin/env python3
"""
gui_interface.py — WIM Terminal (2-panel: CH1 Tones + Result)
"""

import os
import re
import socket
import threading
import tkinter as tk
from collections import deque
from tkinter import font as tkfont

_LOGO_PATH = os.path.join(os.path.dirname(__file__), 'adj_logo_small.png')

# ── Config ────────────────────────────────────────────────────────────────────
IND570_HOST = "192.168.0.1"
IND570_PORT = 1702
AVG_MAX     = 1000
KG_TO_KN    = 0.00981

_LINE_RE = re.compile(r'([+-]?\d+\.?\d*)\s*(kg|g|lb|t)', re.IGNORECASE)

def _parse_line(line: str) -> float | None:
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
    return value   # always kg

# ── Shared state ──────────────────────────────────────────────────────────────

class SharedState:
    def __init__(self, avg_win: int = 100):
        self._lock     = threading.Lock()
        self.valid     = 0
        self.result_kg = 0.0
        self.result_kn = 0.0
        self.bytes_rx  = 0
        self._buf      = deque(maxlen=max(1, avg_win))

    def set_avg_window(self, n: int):
        with self._lock:
            self._buf = deque(maxlen=max(1, min(n, AVG_MAX)))

    def reset_zero(self):
        with self._lock:
            self._buf.clear()




    def push(self, kg: float, bytes_rx: int):
        with self._lock:
            self._buf.append(kg)
            avg = sum(self._buf) / len(self._buf)
            self.result_kg = avg
            self.result_kn = avg * KG_TO_KN
            self.bytes_rx  = bytes_rx
            self.valid     = 1

    def set_error(self):
        with self._lock:
            self.valid = -1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                'valid':     self.valid,
                'result_kg': self.result_kg,
                'result_kn': self.result_kn,
                'bytes_rx':  self.bytes_rx,
            }

# ── TCP reader thread ─────────────────────────────────────────────────────────

def reader_thread(state: SharedState, running: threading.Event):
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

# ── GUI ───────────────────────────────────────────────────────────────────────

BG        = '#ffffff'
BG2       = '#f0f0f0'
DISP_BG   = '#e8e8e8'
DISP_FG   = '#000000'
ACCENT    = '#000000'
GOLD      = '#1a1a1a'
BORDER    = '#cccccc'
LBL_FG    = '#333333'
HDR_BG    = '#f5f5f5'


class NumDisplay(tk.Frame):
    def __init__(self, parent, font, fg=DISP_FG, **kw):
        super().__init__(parent, bg=DISP_BG,
                         highlightbackground=BORDER,
                         highlightthickness=1, **kw)
        self._lbl = tk.Label(self, text='0', font=font,
                             bg=DISP_BG, fg=fg,
                             anchor='center', padx=12, pady=6)
        self._lbl.pack(fill='both', expand=True)

    def set_text(self, text: str):
        self._lbl.config(text=text)


def _seg_display(parent, font=None, fg=DISP_FG) -> NumDisplay:
    return NumDisplay(parent, font=font, fg=fg)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WIM Terminal")
        self.configure(bg=BG)
        self.resizable(True, True)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")
        self.state('zoomed') if self.tk.call('tk', 'windowingsystem') == 'win32' \
            else self.attributes('-zoomed', True)

        self._state   = SharedState(avg_win=100)
        self._running = threading.Event()
        self._running.set()
        self._mode       = tk.StringVar(value='kg → kg')  # default
        self._base_kg    = None
        self._tare_kg    = 0.0
        self._zero_hold      = False   # True during the 10-second freeze after zero
        self._zero_after_id  = None    # after() ID so Live can cancel it

        self._fn_label  = tkfont.Font(family='Helvetica', size=12, weight='bold')
        self._fn_disp   = tkfont.Font(family='Courier',   size=30, weight='bold')
        self._fn_result = tkfont.Font(family='Courier',   size=52, weight='bold')
        self._fn_title  = tkfont.Font(family='Helvetica', size=18, weight='bold')
        self._fn_ch     = tkfont.Font(family='Helvetica', size=13, weight='bold')

        self._build_ui()
        self._start_reader()
        self.after(100, self._update_ui)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self, parent):
        hdr = tk.Frame(parent, bg=HDR_BG, height=130)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)

        if os.path.exists(_LOGO_PATH):
            self._logo_img = tk.PhotoImage(file=_LOGO_PATH)
            tk.Label(hdr, image=self._logo_img, bg=HDR_BG).pack(side='left', padx=(16, 0))

        tk.Label(hdr, text='WIM Terminal',
                 font=self._fn_title, bg=HDR_BG, fg=ACCENT).pack(side='left', padx=(12, 0))

        tk.Button(hdr, text='✕  Close', command=self._on_close,
                  bg='#ffcccc', fg='black', activebackground='#ffaaaa',
                  activeforeground='black', font=self._fn_label,
                  relief='flat', padx=12, pady=4, cursor='hand2').pack(
                      side='right', padx=(4, 12))

        self._conn_dot = tk.Label(hdr, text='●', font=self._fn_label,
                                  bg=HDR_BG, fg='#ff4444')
        self._conn_dot.pack(side='right', padx=4)
        tk.Label(hdr, text='Connection', font=self._fn_label,
                 bg=HDR_BG, fg=LBL_FG).pack(side='right', padx=(16, 0))

    # ── Main: CH1 Tones  |  Result ───────────────────────────────────────────

    def _build_main(self, parent):
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill='both', expand=True, padx=8, pady=(4, 0))

        # ── CH 1 Tones panel ─────────────────────────────────────────────────
        ch_lf = tk.LabelFrame(outer, text='  CH 1  ',
                              bg=BG2, fg=ACCENT,
                              font=self._fn_ch,
                              highlightbackground=BORDER,
                              padx=20, pady=16, bd=2, relief='groove')
        ch_lf.pack(side='left', fill='both', expand=True, padx=(0, 6))

        tk.Label(ch_lf, text='Tones', bg=BG2, fg=LBL_FG,
                 font=self._fn_label).pack()
        self._ch1_tones_disp = _seg_display(ch_lf, font=self._fn_result, fg=DISP_FG)
        self._ch1_tones_disp.pack(fill='both', expand=True, pady=(4, 0))
        self._ch1_tones_disp.set_text('0.000')

        # ── Result panel ─────────────────────────────────────────────────────
        res_lf = tk.LabelFrame(outer, text='  Result  ',
                               bg=BG2, fg=GOLD,
                               font=self._fn_ch,
                               padx=20, pady=16, bd=2, relief='groove')
        res_lf.pack(side='left', fill='both', expand=True)

        res_top = tk.Frame(res_lf, bg=BG2)
        res_top.pack(fill='x')

        tk.Label(res_top, text='S =', bg=BG2, fg=GOLD,
                 font=self._fn_disp).pack(side='left', padx=(0, 8))
        self._result_disp = _seg_display(res_top, font=self._fn_result, fg=GOLD)
        self._result_disp.pack(side='left', ipadx=20, ipady=12)
        self._result_disp.set_text('0.00')

        self._unit_label_var = tk.StringVar(value='kg')
        tk.Label(res_top, textvariable=self._unit_label_var,
                 bg=BG2, fg=GOLD, font=self._fn_disp).pack(side='left', padx=(8, 0))

        res_btns = tk.Frame(res_lf, bg=BG2)
        res_btns.pack(fill='x', pady=(12, 0))

        # Mode dropdown
        tk.Label(res_btns, text='MODE', bg=BG2, fg=LBL_FG,
                 font=self._fn_label).pack(side='left', padx=(0, 4))
        MODES = ['kg → kg', 'kg → Tons', 'kg → 2×Tons', 'kg → 2kg']
        om = tk.OptionMenu(res_btns, self._mode, *MODES)
        om.configure(width=14, font=self._fn_label,
                     bg=BG2, fg=ACCENT, activebackground=BORDER,
                     activeforeground=ACCENT, highlightthickness=0, relief='flat')
        om['menu'].configure(bg=BG2, fg=ACCENT, font=self._fn_label)
        om.pack(side='left', padx=(0, 20))

        # Delta + Zero buttons
        self._record_btn = tk.Button(res_btns, text='Δ  Delta',
                                     font=self._fn_label,
                                     bg='#cce0ff', fg='black',
                                     activebackground='#aaccff',
                                     activeforeground='black',
                                     relief='flat', padx=14, pady=8,
                                     cursor='hand2',
                                     command=self._on_record)
        self._record_btn.pack(side='left', padx=(0, 8))

        tk.Button(res_btns, text='⊙  Zero', command=self._on_zero,
                  bg='#ffe0b2', fg='black', activebackground='#ffcc80',
                  activeforeground='black', font=self._fn_label,
                  relief='flat', padx=14, pady=8, cursor='hand2').pack(side='left', padx=(0, 8))

        tk.Button(res_btns, text='⬤  Live', command=self._on_live,
                  bg='#ccffcc', fg='black', activebackground='#aaffaa',
                  activeforeground='black', font=self._fn_label,
                  relief='flat', padx=14, pady=8, cursor='hand2').pack(side='left')

        self._base_label_var = tk.StringVar(value='')
        tk.Label(res_lf, textvariable=self._base_label_var,
                 bg=BG2, font=self._fn_label).pack(anchor='w')

    # ── Bottom status bar ─────────────────────────────────────────────────────

    def _build_bottom(self, parent):
        bot = tk.Frame(parent, bg=BG)
        bot.pack(fill='x', padx=6, pady=(2, 6))

        tk.Label(bot, text='Average by', bg=BG,
                 font=self._fn_label).pack(side='left')
        self._avg_var = tk.IntVar(value=100)
        sp = tk.Spinbox(bot, from_=1, to=AVG_MAX, width=5,
                        textvariable=self._avg_var,
                        font=self._fn_label,
                        command=self._on_avg_changed)
        sp.pack(side='left', padx=4)
        tk.Label(bot, text='measurements', bg=BG,
                 font=self._fn_label).pack(side='left')

        self._status_var = tk.StringVar(value='Bytes on port: 0')
        tk.Label(bot, textvariable=self._status_var, bg=BG,
                 font=self._fn_label, anchor='e').pack(
                     side='right', fill='x', expand=True)

    # ── Assemble ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header(self)
        self._build_main(self)
        self._build_bottom(self)

    # ── Periodic UI update ────────────────────────────────────────────────────

    def _resume_after_zero(self):
        self._zero_hold = False

    def _update_ui(self):
        if self._zero_hold:
            self.after(100, self._update_ui)
            return

        d = self._state.snapshot()

        if d['valid'] == -1:
            self._status_var.set(f"ERROR: Cannot connect to {IND570_HOST}:{IND570_PORT}")
            self._conn_dot.config(fg='#ff4444')
        elif d['valid'] == 1:
            self._conn_dot.config(fg='#44cc44')
            kg   = d['result_kg'] - self._tare_kg
            mode = self._mode.get()

            if mode == 'kg → kg':
                s_val = kg
                unit  = 'kg'
                if self._base_kg is not None:
                    ch1_val = self._base_kg + (kg - self._base_kg) / 1000.0
                else:
                    ch1_val = kg
            elif mode == 'kg → Tons':
                s_val = kg
                unit  = 'T'
                if self._base_kg is not None:
                    ch1_val = self._base_kg + (kg - self._base_kg) / 1000.0
                else:
                    ch1_val = kg
            elif mode == 'kg → 2×Tons':
                s_val = kg * 2
                unit  = 'T'
                if self._base_kg is not None:
                    ch1_val = self._base_kg + (kg - self._base_kg) / 1000.0
                else:
                    ch1_val = kg
            else:   # kg → 2kg
                s_val = kg * 2
                unit  = 'kg'
                if self._base_kg is not None:
                    ch1_val = self._base_kg + (kg - self._base_kg) / 1000.0
                else:
                    ch1_val = kg

            self._ch1_tones_disp.set_text(f"{ch1_val:.3f}")
            self._result_disp.set_text(f"{s_val:.2f}")
            self._unit_label_var.set(unit)
            self._status_var.set(f"Bytes received: {d['bytes_rx']}")

        self.after(100, self._update_ui)

    # ── Callbacks ─────────────────────────────────────────────────────────────



    def _start_reader(self):
        threading.Thread(target=reader_thread,
                         args=(self._state, self._running),
                         daemon=True).start()

    def _on_record(self):
        d = self._state.snapshot()
        if d['valid'] == 1:
            self._base_kg = d['result_kg'] - self._tare_kg
            self._base_label_var.set(f"Base: {self._base_kg:.2f} kg")

    def _on_zero(self):
        d = self._state.snapshot()
        self._tare_kg   = d['result_kg']
        self._base_kg   = None
        self._zero_hold = True
        self._base_label_var.set('')
        self._ch1_tones_disp.set_text('0.000')
        self._result_disp.set_text('0.00')
        self._zero_after_id = self.after(10000, self._resume_after_zero)

    def _on_live(self):
        if self._zero_after_id is not None:
            self.after_cancel(self._zero_after_id)
            self._zero_after_id = None
        self._zero_hold = False

    def _on_avg_changed(self):
        self._state.set_avg_window(self._avg_var.get())

    def _on_close(self):
        self._running.clear()
        self.destroy()

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    App().mainloop()

if __name__ == '__main__':
    main()
