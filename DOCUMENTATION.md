# Strain Gauge WIM Terminal — Project Documentation

**ADJ Engineering Pvt. Ltd.**
Last updated: 2026-05-08

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Hardware Setup](#2-hardware-setup)
3. [Packet Format](#3-packet-format)
4. [Calibration](#4-calibration)
5. [File Reference](#5-file-reference)
6. [Building the C Tools](#6-building-the-c-tools)
7. [Running Each Tool](#7-running-each-tool)
8. [WIM Terminal GUI (gui_interface.py)](#8-wim-terminal-gui-gui_interfacepy)
9. [Network & Serial Configuration](#9-network--serial-configuration)
10. [Modes Explained](#10-modes-explained)
11. [Delta (Δ) Feature](#11-delta-δ-feature)
12. [Zero Feature](#12-zero-feature)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Project Overview

This project is a **Weigh-In-Motion (WIM) terminal** for a 4-sensor load cell scale used by ADJ Engineering Pvt. Ltd. It reads weight data from a physical weighing terminal (IND570 or compatible), converts it, and displays it in a GUI.

### Data flow

```
Physical Scale (4 load cells)
        │
        │  USB Serial  (45-byte binary packets)
        ▼
  [read_usb.py]          ← low-level USB reader / debugger
  [basic_interface.c]    ← GTK GUI reading raw USB serial packets
        │
        │  TCP stream (plain-text kg values)  192.168.0.1:1702
        ▼
  [reader.c]             ← terminal live viewer (CLI)
  [bridge.c]             ← converts TCP stream → serial binary packets
  [gui_interface.py]     ← main WIM Terminal GUI (Python/Tkinter)
        │
        │  Virtual serial port  /tmp/ttyV0 ↔ /tmp/ttyV1
        ▼
  Company software (TVEMA or equivalent)
```

---

## 2. Hardware Setup

| Component | Detail |
|-----------|--------|
| Scale | 4-corner load cell platform |
| Sensors | 4 × Wheatstone bridge strain gauges (S0–S3) |
| Each sensor | 4 bridge arms (P1–P4) |
| Controller | IND570 weighing terminal |
| USB output | 45-byte binary packets @ 115200 baud |
| TCP output | Plain-text weight stream on port 1702 |
| Host machine IP | `192.168.0.100` |
| Terminal IP | `192.168.0.1` |

---

## 3. Packet Format

Every measurement is transmitted as a **45-byte binary packet** over USB serial or virtual serial.

```
Byte  0        : 0x80          ← start-of-frame header
Bytes  1 – 11  : Group 0       ← 0xC0 | cfg(2) | P1(2) | P2(2) | P3(2) | P4(2)
Bytes 12 – 22  : Group 1       ← 0xC1 | cfg(2) | P1(2) | P2(2) | P3(2) | P4(2)
Bytes 23 – 33  : Group 2       ← 0xC2 | cfg(2) | P1(2) | P2(2) | P3(2) | P4(2)
Bytes 34 – 44  : Group 3       ← 0xC3 | cfg(2) | P1(2) | P2(2) | P3(2) | P4(2)
```

- All multi-byte values are **little-endian signed int16**
- `cfg` — 2-byte hardware ADC config register, constant per channel, not used in weight calculation
- `P1–P4` — raw ADC counts from the 4 arms of the Wheatstone bridge for that sensor
- Markers `0xC0`–`0xC3` appear at fixed byte offsets; the parser uses these to validate a packet

### Packet validation

A packet is considered valid when:
- `byte[0]  == 0x80`
- `byte[1]  == 0xC0`
- `byte[12] == 0xC1`
- `byte[23] == 0xC2`
- `byte[34] == 0xC3`

---

## 4. Calibration

Each sensor has 4 Wheatstone bridge arms. Weight is derived by comparing each arm's raw count against a known zero offset and sensitivity.

### Formula

```
arm_kg[a]   = (P[a] - ZERO[sensor][a]) / SENS[sensor][a]
sensor_kg   = mean(arm_kg[0..3])
total_kg    = sensor_kg[0] + sensor_kg[1] + sensor_kg[2] + sensor_kg[3]
```

### Zero offsets (raw counts at 0 kg, captured 2026-04-15)

| Sensor | P1      | P2       | P3     | P4       |
|--------|---------|----------|--------|----------|
| S0     | -755.0  | -2594.7  | 1361.0 | -3485.7  |
| S1     | -752.3  | -2595.0  | 1359.3 | -3483.7  |
| S2     | -751.0  | -2595.7  | 1359.3 | -3483.0  |
| S3     | -749.0  | -2597.3  | 1357.0 | -3483.7  |

### Sensitivity (counts per kg, derived from 665 kg capture)

| Sensor | P1     | P2     | P3     | P4     |
|--------|--------|--------|--------|--------|
| S0     | 1.6186 | 1.2491 | 1.0482 | 0.9479 |
| S1     | 1.6026 | 1.2511 | 1.0585 | 0.9360 |
| S2     | 1.5947 | 1.2555 | 1.0586 | 0.9314 |
| S3     | 1.5825 | 1.2652 | 1.0723 | 0.9357 |

> **Note:** Calibration is based on a single-point 665 kg capture. Accuracy is best above ~200 kg. Noise floor near zero is approximately ±5 kg.

---

## 5. File Reference

| File | Language | Purpose |
|------|----------|---------|
| `gui_interface.py` | Python | **Main GUI** — WIM Terminal display (CH1 Tones + Result) |
| `read_usb.py` | Python | Low-level USB serial reader and packet decoder/debugger |
| `basic_interface.c` | C / GTK3 | 4-channel GTK GUI reading direct USB serial |
| `reader.c` | C | CLI live weight viewer connecting to TCP stream |
| `bridge.c` | C | Converts TCP weight stream to binary serial packets |
| `replay.c` | C | Replays captured binary frames to test serial connection |
| `check_packet.c` | C | Compares generated packets against captured frames |
| `Makefile` | Make | Builds all C tools |
| `install_windows.bat` | Batch | Windows installation helper |
| `adj_logo_small.png` | Image | Logo shown in GUI header |
| `hex_dumps.txt` | Text | Reference hex captures for packet analysis |
| `StrainGauge.spec` | PyInstaller | Spec file for building standalone executable |

---

## 6. Building the C Tools

### Prerequisites

```bash
# Ubuntu / Debian
sudo apt install build-essential libgtk-3-dev

# Fedora / RHEL
sudo dnf install gcc gtk3-devel
```

### Build all

```bash
make
```

### Build individually

```bash
gcc -Wall -O2 -o reader         reader.c         -lpthread -lm
gcc -Wall -O2 -o bridge         bridge.c         -lm
gcc -Wall -O2 -o replay         replay.c
gcc -Wall -O2 -o check_packet   check_packet.c
gcc -Wall -O2 -o basic_interface basic_interface.c \
    $(pkg-config --cflags --libs gtk+-3.0) -lpthread -lm
```

### Clean

```bash
make clean
```

---

## 7. Running Each Tool

### `read_usb.py` — USB packet debugger

Reads directly from the USB serial port and decodes each 45-byte packet.

```bash
python3 read_usb.py                          # default /dev/ttyUSB0 @ 115200
python3 read_usb.py --port /dev/ttyUSB1
python3 read_usb.py --raw                    # also prints raw hex bytes
python3 read_usb.py --baud 9600 19200 115200 # auto-probe baud rate
```

Output shows per-sensor P1–P4 counts, per-sensor kg, instant total kg, and a rolling 30-packet average.

---

### `reader` — CLI live weight viewer

Connects to the IND570 TCP stream and displays live weight with statistics.

```bash
./reader
```

Keys while running:

| Key | Action |
|-----|--------|
| `1` | Mode 1 — real-time kg |
| `2` | Mode 2 — 1 kg = 1 ton |
| `3` | Mode 3 — 1 kg = 2 ton |
| `Ctrl+C` | Stop |

Displays live weight, min/max/mean of last 100 readings.

---

### `bridge` — TCP to serial bridge

Reads the TCP plain-text weight stream and writes binary 45-byte packets to a virtual serial port so the company software (TVEMA) can receive them.

**Step 1** — create a virtual serial port pair:
```bash
socat -d -d pty,raw,echo=0,link=/tmp/ttyV0 pty,raw,echo=0,link=/tmp/ttyV1
```

**Step 2** — connect company software to `/tmp/ttyV1`

**Step 3** — run the bridge:
```bash
./bridge
```

Scale factor `SCALE = 10` means 0.1 kg resolution, max 3276.7 kg. To change to 0.01 kg resolution (max 327.6 kg), set `SCALE = 100` in `bridge.c` and recompile.

---

### `replay` — serial frame replayer

Sends captured binary frames in a loop to test the serial connection without a live scale.

```bash
# Step 1: start socat (same as bridge)
socat -d -d pty,raw,echo=0,link=/tmp/ttyV0 pty,raw,echo=0,link=/tmp/ttyV1

# Step 2: run replay
./replay
```

Sends at 100 Hz (10 ms per packet). Press `Ctrl+C` to stop.

---

### `check_packet` — packet format verification

Builds a test packet for 50.4 kg and compares it byte-by-byte against a real captured frame. Useful when modifying the packet format.

```bash
./check_packet
```

---

### `basic_interface` — GTK 4-channel interface

A GTK3 GUI that reads directly from `/dev/ttyUSB0` and shows all 4 channels with their raw q counts, Tones values, and a combined result.

```bash
./basic_interface
```

Requires the USB sensor to be connected. Use the kg/kN radio buttons in the Result panel to switch units. "Set Zero" clears the rolling average buffer.

---

### `gui_interface.py` — Main WIM Terminal GUI

The primary production GUI. Connects to the IND570 over TCP.

```bash
python3 gui_interface.py
```

See [Section 8](#8-wim-terminal-gui-gui_interfacepy) for full details.

---

## 8. WIM Terminal GUI (`gui_interface.py`)

### Layout

```
┌─────────────────────────────────────────────────────────┐
│  [Logo]  WIM Terminal          Connection ●  ✕ Close    │
├──────────────────────────┬──────────────────────────────┤
│  CH 1                    │  Result                      │
│  ┌──────────────────┐    │  S = ┌──────────┐  kg        │
│  │  Tones           │    │      │          │            │
│  │                  │    │  MODE [kg → kg (Δ) ▾]        │
│  └──────────────────┘    │  [Δ Delta]  [⊙ Zero]         │
│                          │                              │
├──────────────────────────┴──────────────────────────────┤
│  Average by [100 ↕] measurements       Bytes received:  │
└─────────────────────────────────────────────────────────┘
```

### Panels

**CH 1 (left panel)**
- Shows the **Tones** value for channel 1
- Value changes based on the selected mode and Delta base

**Result (right panel)**
- `S =` — the live result value in the selected unit
- MODE dropdown — selects the conversion mode
- `Δ Delta` button — captures the current weight as a base for delta calculation
- `⊙ Zero` button — zeroes both displays

**Bottom bar**
- Average window spinner (1–1000 measurements)
- Live byte count / connection error message

### Connection

The GUI connects to `192.168.0.1:1702` via TCP in a background thread. The connection dot in the header shows:
- **Red (●)** — not connected or error
- **Green (●)** — receiving data

On disconnect the thread retries every 2 seconds automatically.

---

## 9. Network & Serial Configuration

### TCP (gui_interface.py, reader.c, bridge.c)

| Parameter | Value |
|-----------|-------|
| Terminal IP | `192.168.0.1` |
| Port | `1702` |
| Host machine IP | `192.168.0.100` |
| Protocol | Plain ASCII, one weight value per line, e.g. `"      6.10 kg \r\n"` |
| Supported units | `kg`, `g`, `lb`, `t` (all converted to kg internally) |

To change the terminal IP, edit `IND570_HOST` in `gui_interface.py` or `TCP_HOST` in the C files.

### USB Serial (read_usb.py, basic_interface.c)

| Parameter | Value |
|-----------|-------|
| Port | `/dev/ttyUSB0` |
| Baud rate | `115200` |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |

If permission is denied on the serial port:
```bash
sudo usermod -aG dialout $USER
# log out and back in
```

---

## 10. Modes Explained

The MODE dropdown in the GUI controls how the incoming kg value is converted for display.

| Mode | S = (Result) | CH1 Tones |
|------|-------------|-----------|
| `kg → kg (Δ)` | Live kg | Live kg (or delta decimal if base set) |
| `kg → Tons (Δ)` | Live kg | Live kg (or delta decimal if base set) |
| `kg → 2×kN (Δ)` | Live kg × 2 | Live kg (or delta decimal if base set) |
| `kg → 2kg (Δ)` | Live kg × 2 | Live kg (or delta decimal if base set) |

All modes share the same Delta behaviour — see Section 11.

---

## 11. Delta (Δ) Feature

The Delta feature captures a **base weight** at a point in time. After that, CH1 Tones displays the weight as a small decimal offset from that base, making it easy to see small changes.

### How to use

1. Wait for a stable reading
2. Press **Δ Delta** — the current weight is saved as the base
3. CH1 Tones now shows: `base + (current − base) / 1000`

**Example:** Base = 500 kg, current = 520 kg
```
CH1 Tones = 500 + (520 - 500) / 1000 = 500.020
```

This makes a 20 kg change appear as `0.020` in the decimal portion, useful for monitoring small incremental loads on a large base.

The base label below the buttons shows the captured base value (e.g. `Base: 500.00 kg`).

To reset the delta, press **⊙ Zero**.

---

## 12. Zero Feature

Pressing **⊙ Zero**:

1. Captures the current live weight as a **tare offset** — all subsequent readings are relative to this point (both displays show 0)
2. Clears the Delta base
3. Both displays are immediately set to `0.000` / `0.00`
4. After **10 seconds**, the displays resume showing live weight (relative to the new tare)

The 10-second freeze gives time to remove whatever was on the scale before taring, or to confirm the zero visually.

---

## 13. Troubleshooting

### GUI shows "ERROR: Cannot connect"

- Check that the IND570 terminal is powered on and connected to the network
- Verify the host machine IP is `192.168.0.100` and can reach `192.168.0.1`
- Check firewall rules for port 1702

```bash
ping 192.168.0.1
telnet 192.168.0.1 1702
```

### `read_usb.py` shows no packets

- Check USB cable and port: `ls /dev/ttyUSB*`
- Try `--baud 9600 19200 115200` to auto-probe the baud rate
- Check permissions: `sudo usermod -aG dialout $USER`

### `bridge` cannot open serial port

- Make sure `socat` is running first (creates `/tmp/ttyV0` and `/tmp/ttyV1`)
- Check `SERIAL_PORT` in `bridge.c` matches the socat output path

### Weight reads incorrectly near zero

- Calibration is accurate above ~200 kg; noise floor near zero is ±5 kg
- Use the **⊙ Zero** button to tare after the scale has warmed up

### CH1 Tones shows unexpected decimal values

- A Delta base may be set — press **⊙ Zero** to clear it
- Check the "Base:" label below the Delta button to see if a base is active
