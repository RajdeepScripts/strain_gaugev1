# WIM Terminal — System Overview (Current System)

**ADJ Engineering Pvt. Ltd.**
Covers the current production path only: `basic_interface_ind570.py` (PyQt5 GUI) reading from a **Mettler-Toledo IND570** weighing terminal wired to a **Mettler-Toledo load cell** (not a raw strain-gauge bridge).

> The older USB-packet / Wheatstone-arm architecture described in `DOCUMENTATION.md` (`bridge.c`, `basic_interface.c`, `gui_interface.py`, 45-byte binary packets) is retained there as historical reference. It is **not** part of the current system and is not covered here.

---

## 1. What the system is

A live weight-display application for a weighbridge/WIM (Weigh-In-Motion) setup:

```
Mettler-Toledo load cell
        │  (analog load signal)
        ▼
   IND570 terminal            ← does ADC, scaling, and outputs a weight stream
        │  Ethernet (TCP), plain ASCII text
        ▼
basic_interface_ind570.py     ← this application (PyQt5 GUI)
        │
        ▼
   On-screen 4-channel display (7-segment style)
```

The IND570 is the only thing that talks to the load cell directly. The application never touches the sensor signal — it only reads a text stream the IND570 already produces over the network. See `ETHERNET_DATA_TRANSMISSION.md` for the full detail on that link.

---

## 2. Why a "4-channel" layout, with one physical load cell

The UI (`ChannelColumn`, "4x4 Basic Interface") renders **four identical channel columns**, but the current hardware is a single IND570 + single Mettler-Toledo load cell feed. All four columns read the same underlying value from `SharedState` — the 4-channel layout is inherited from an earlier 4-load-cell platform design and kept for UI consistency. There is currently no per-channel hardware wiring; it is one live value replicated across four display columns.

---

## 3. Application architecture

### 3.1 Process / threading model

| Component | Type | Responsibility |
|---|---|---|
| `reader_thread()` | Background `threading.Thread` (daemon) | Owns the TCP socket to the IND570; parses incoming lines; pushes values into `SharedState` |
| `SharedState` | Thread-safe object (`threading.Lock`) | Holds the latest averaged result, validity flag, byte counter; read/written from both the reader thread and the UI thread |
| `MainWindow` (Qt `QMainWindow`) | Main/UI thread | Builds the window, drives a `QTimer` at 100 ms to poll `SharedState` and redraw |

The reader thread and the UI thread never touch each other's data directly — everything passes through `SharedState`'s locked `push()` / `snapshot()` methods. This is the standard pattern for keeping a Qt UI responsive while a blocking socket read happens elsewhere.

### 3.2 Startup sequence

1. `main()` creates the `QApplication`, sets the `Fusion` style, and constructs `MainWindow`.
2. `MainWindow.__init__` builds all UI panels, creates a `SharedState(avg_win=100)`, sets a `threading.Event` (`_running`) to signaled, and starts `reader_thread` as a daemon thread.
3. A `QTimer` fires every 100 ms calling `_update_ui()`, which is the only place the UI actually reflects new data.
4. The reader thread independently loops: connect → read → parse → push, retrying every 2 seconds on failure, for as long as `_running` stays set.

### 3.3 Shutdown

`MainWindow.closeEvent` clears the `_running` event, which lets the reader thread's loop exit on its next check; the thread is a daemon so it won't block process exit even if it's mid-`recv()`.

---

## 4. UI layout

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

### 4.1 Top bar — "COM-Port controls"

A `QGroupBox` with **Start / Stop** buttons, a port dropdown pre-populated with `COM7` and `192.168.0.1:1702`, and **Update port list** / **Save port name** buttons.

**Important:** this entire group is cosmetic/legacy. None of these controls read or change the actual connection target — the app always connects to the hardcoded `IND570_HOST:IND570_PORT` (`basic_interface_ind570.py:22-23`). Start/Stop do work (they toggle the reader thread via `_running`), but the COM port dropdown and its buttons are not wired to anything.

### 4.2 Channel columns (×4)

Each `ChannelColumn`:
- **Title** — "Chanel N"
- **Top 7-segment display** — currently always shows a static `"0"` (or blank when zeroed); not driven by live data in the current build
- **temp =** — read-only field, always `"0"` (no temperature input exists in this system)
- **Two "k =" fields** — free-text coefficient inputs, default `0.0067563`; present in the UI but not read by any calculation in the current code
- **Value display (7-segment) + unit label** — this is the live figure: shows the averaged/converted weight, updated every 100 ms
- **Calibration value** field + **Calibrate** button — sets a per-column additive offset (`_cal_offset`) applied to that column's displayed value only
- **Zero** / **Cancel zero** buttons — per-column blanking (shows `" 0"` on both displays), independent of the other three columns and independent of the global tare

### 4.3 Bottom bar

- **Average by N measurements** (`QSpinBox`, 1–1000) — live-adjusts the rolling-average window size in `SharedState`
- **Mode** dropdown — `kg`, `2 × kg`, `Tons`, `2 × Tons`; scales the live kg value and changes the unit label (see §5)
- **Δ Delta** — captures current value as a base for all four columns (see §5.1)
- **Live** — clears the delta base, returns to live display
- **Base: …** label — shows the currently captured delta base, blank when not set
- **Last packages / Bytes on port** — status line; shows a red error message if the TCP connection is down
- **Exit program** — closes the window

---

## 5. Modes and calculations

| Mode | Displayed value | Unit shown |
|---|---|---|
| `kg` | live averaged kg | `kg` |
| `2 × kg` | live averaged kg × 2 | `kg` |
| `Tons` | live averaged kg (no conversion factor applied) | `T` |
| `2 × Tons` | live averaged kg × 2 | `T` |

> Note: "Tons" mode currently displays the same numeric value as `kg` mode, just with the unit label changed to `T` — there is no ÷1000 conversion applied in `_update_ui` or `_on_mode_changed`. If a true kg→ton conversion is required, this is a code change, not a configuration option.

### 5.1 Delta (Δ) feature

- Pressing **Δ Delta** captures the current mode-adjusted value and the current raw kg (`_on_delta`), storing both as the "base" for every channel column.
- While a base is set, each column's value display shows:
  `base + (current_raw_kg − base_raw_kg) / 1000`
  i.e., the base plus a small decimal representing the change since the base was captured — useful for watching small incremental loads relative to a much larger base weight without losing resolution.
- **Live** clears the base on all columns and reverts to showing the live value directly.

### 5.2 Per-column Zero / Cancel zero

Independent of Delta: pressing a column's **Zero** button blanks that column's two displays to `0`; **Cancel zero** resumes normal display. This does not affect the global average, the other columns, or the calibration offset.

### 5.3 Per-column Calibrate

Typing a number into **Calibration value** and pressing **Calibrate** stores it as `_cal_offset`, added directly to that column's displayed value only (`update_value`, basic_interface_ind570.py:329-347). It does not affect the underlying averaged kg in `SharedState`, only what that one column shows.

---

## 6. Averaging and smoothing

- `SharedState` keeps a `collections.deque` capped at the configured window size (default 100, adjustable 1–1000 via the spinner).
- Every parsed kg value from the IND570 stream is appended; the displayed result is the arithmetic mean of everything currently in the deque.
- Changing the spinner value creates a **new, empty** deque at the new size (`set_avg_window`) — the average resets and starts refilling immediately, it does not resample the old buffer.

---

## 7. Error / connection states

`_update_ui()` checks `SharedState.valid`:

| `valid` | Meaning | UI behavior |
|---|---|---|
| `0` | No data yet | Status bar unchanged from initial text |
| `1` | Receiving data normally | Status bar shows byte count in black; channel displays update |
| `-1` | Socket exception (connect/read failed) | Status bar shows `ERROR: Cannot connect to <host>:<port>` in red; channel displays stop updating (last value stays on screen) |

The reader thread retries the connection every 2 seconds indefinitely while `_running` is set — no manual reconnect action is needed if the IND570 or network comes back.

---

## 8. Build / packaging

- **Entry point:** `basic_interface_ind570.py`
- **Dependency:** `PyQt5` (see `requirements.txt`)
- **Packaging:** `StrainGauge.spec` (PyInstaller) bundles `adj_logo_small.png` and `adj_logo.jpeg` alongside the executable, named `StrainGauge`, windowed (no console)
- **CI:** `.github/workflows/build-windows.yml` builds `StrainGauge.exe` on `windows-latest` on every push to `main` and on version tags (`v*`), uploading it as a workflow artifact and attaching it to the GitHub Release for tagged builds

---

## 9. Known gaps between UI and behavior (for future cleanup)

These are not bugs preventing operation, but UI elements that currently do nothing or less than they visually imply — worth knowing so they aren't mistaken for configuration options:

- COM-port dropdown, **Update port list**, **Save port name** — not wired to the connection (see §4.1)
- **Save coefficients** button (top-right) — not wired to anything
- Top 7-segment display per column, **temp =** field, both **k =** fields — not driven by any live calculation
- **Tons** / **2 × Tons** modes — do not apply a kg→ton scale factor, only relabel the unit

If any of these are meant to become functional (e.g., configurable IND570 host/port from the dropdown, real per-arm coefficients, true ton conversion), that's a scoped follow-up — flag which ones matter and it can be planned separately.
