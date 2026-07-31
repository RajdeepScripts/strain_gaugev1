# WIM Terminal — System Overview

Covers `basic_interface_ind570.py` — PyQt5 GUI, reads from a Mettler-Toledo IND570 weighing terminal wired to a Mettler-Toledo load cell.

(Old USB-packet / Wheatstone-arm stuff — bridge.c, basic_interface.c, gui_interface.py — is gone. This is the only app now.)

---

## What it does

```
Mettler-Toledo load cell
        │  analog signal
        ▼
   IND570 terminal        ← ADC + scaling, outputs weight over TCP
        │  Ethernet, ASCII text
        ▼
basic_interface_ind570.py
        │
        ▼
   4-column on-screen display (7-segment style)
```

IND570 talks to the load cell. The app just reads a text stream off the network — see `ETHERNET_DATA_TRANSMISSION.md` for that part.

---

## Why 4 channels for 1 load cell

UI shows 4 identical columns (`ChannelColumn`), but there's one IND570 feed. All 4 read the same value from `SharedState`. Leftover from an older 4-load-cell layout — kept for consistency, not because there's 4 sensors.

---

## Threading

- `reader_thread()` — daemon thread, owns the TCP socket, parses lines, pushes into `SharedState`
- `SharedState` — lock-protected, holds latest average + validity flag + byte count
- `MainWindow` — UI thread, `QTimer` polls `SharedState` every 100ms and redraws

Reader thread and UI thread never touch each other directly, only through `SharedState.push()` / `.snapshot()`.

Startup: `main()` → `QApplication` → `MainWindow.__init__` builds UI, starts reader thread, starts the 100ms timer. Reader loop: connect → read → parse → push, retry every 2s on failure.

Shutdown: `closeEvent` clears the running flag, reader thread exits on next check (daemon, so it won't block exit either way).

---

## UI layout

```
┌───────────────────────────────────────────────────────────────────┐
│  [COM-Port controls: Start | Stop | port dropdown | ...]  (legacy) │
│                                          [Save coefficients]       │
│  Chanel data                                                       │
├───────────┬───────────┬───────────┬───────────────────────────────┤
│ Channel 1 │ Channel 2 │ Channel 3 │ Channel 4                      │
│ [7-seg]   │ [7-seg]   │ [7-seg]   │ [7-seg]      (raw display)     │
│ temp =    │ temp =    │ temp =    │ temp =                         │
│ k = ...   │ k = ...   │ k = ...   │ k = ...      (x2 rows each)     │
│ [7-seg] kg│ [7-seg] kg│ [7-seg] kg│ [7-seg] kg   (value display)   │
│ Cal value │ Cal value │ Cal value │ Cal value                      │
│ [Calibrate][Zero][Cancel zero]  (per column, x4)                  │
├───────────┴───────────┴───────────┴───────────────────────────────┤
│ Average by [100] measurements | Mode: [kg ▾] | Δ Delta | Live      │
│ Base: ...   |   Last packages: — Bytes on port: N   | Exit program │
└───────────────────────────────────────────────────────────────────┘
```

**COM-Port controls (top bar)** — Start/Stop actually work (toggle the reader thread). The port dropdown (`COM7`, `192.168.0.1:1702`), "Update port list", and "Save port name" don't do anything — connection target is hardcoded (`IND570_HOST`/`IND570_PORT` in the source). Same for "Save coefficients."

**Each channel column:**
- Title, "Chanel N"
- Top 7-seg display — static "0", not live
- `temp =` — always "0", no temp input exists
- Two `k =` fields — default `0.0067563`, not used in any calc
- Value display + unit — this one's real, updates every 100ms
- Calibration value + Calibrate — adds an offset to that column only
- Zero / Cancel zero — blanks that column only, doesn't touch the global average

**Bottom bar** — average window (1–1000), mode dropdown, Delta/Live, base label, status line, exit.

---

## Modes

| Mode | Value shown | Unit |
|---|---|---|
| kg | avg kg | kg |
| 2 × kg | avg kg × 2 | kg |
| Tons | avg kg (no ÷1000) | T |
| 2 × Tons | avg kg × 2 (no ÷1000) | T |

Tons mode doesn't actually convert — same number as kg, just relabeled. Fix requires a code change if real ton conversion is wanted.

**Delta** — press Δ to grab current value as a base. Display then shows `base + (current_raw_kg − base_raw_kg) / 1000`, so small changes show up as decimals on a big base. Press Live to clear it.

**Zero/Cancel zero** — per-column, independent of Delta and the global average.

**Calibrate** — per-column additive offset, display-only, doesn't touch the underlying average.

---

## Averaging

Rolling average over a `deque`, window size 1–1000 (spinner, default 100). Changing the window resets the buffer — starts fresh, doesn't resample old data.

---

## Connection states

| `valid` | Meaning | UI |
|---|---|---|
| 0 | no data yet | unchanged |
| 1 | receiving | byte count shown, displays update |
| -1 | socket error | red "ERROR: Cannot connect to host:port", last value frozen on screen |

Auto-retries every 2s. No manual reconnect needed.

---

## Build

- Entry point: `basic_interface_ind570.py`
- Dep: PyQt5 (`requirements.txt`)
- Package: `StrainGauge.spec` (PyInstaller), bundles the logo images, windowed exe, name `StrainGauge`
- CI: `.github/workflows/build-windows.yml` builds on `windows-latest`, push to `main` or `v*` tags, uploads exe as artifact / release asset

---

## Known unwired stuff

Not bugs, just UI that doesn't do what it looks like it does:

- COM dropdown, "Update port list", "Save port name" — no-ops
- "Save coefficients" — no-op
- Top 7-seg display, `temp =`, both `k =` fields — not live
- Tons/2×Tons — don't actually scale by 1000

If any of these need to become real, that's separate work — say which ones matter.
