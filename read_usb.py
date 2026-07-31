#!/usr/bin/env python3
"""
read_usb.py — Read and decode 45-byte packets from the weighing sensor via USB serial.

Packet format (45 bytes, little-endian int16):
  Byte  0        : 0x80  start-of-frame header
  Bytes  1 – 11  : Load cell 0  →  0xC0 | cfg(2) | P1(2) | P2(2) | P3(2) | P4(2)
  Bytes 12 – 22  : Load cell 1  →  0xC1 | cfg(2) | P1(2) | P2(2) | P3(2) | P4(2)
  Bytes 23 – 33  : Load cell 2  →  0xC2 | cfg(2) | P1(2) | P2(2) | P3(2) | P4(2)
  Bytes 34 – 44  : Load cell 3  →  0xC3 | cfg(2) | P1(2) | P2(2) | P3(2) | P4(2)

  cfg  : 2-byte hardware config/ADC register — constant per channel, ignore
  P1–P4: 4 arms of a Wheatstone bridge strain gauge on that load cell.
         Each arm measures strain in a different direction — they are NOT
         repeated samples. Some arms go positive under load, others negative.

Usage:
  python3 read_usb.py                          # default /dev/ttyUSB1 @ 115200
  python3 read_usb.py --port /dev/ttyUSB0
  python3 read_usb.py --raw                    # also print raw hex bytes
  python3 read_usb.py --baud 9600 19200 115200 # auto-probe baud rate
"""

import sys
import struct
import argparse
import serial
import time

PORT     = "/dev/ttyUSB0"
BAUD     = 115200
PKT_SIZE = 45

# ── Calibration ───────────────────────────────────────────────────────────────
#
# Physical setup: 4 sensors (C0–C3), one per corner of the scale.
# Each sensor has 4 Wheatstone bridge arms (P1–P4).
# Total weight = sum of all 4 sensor weights.
# Each sensor carries ~1/4 of the total load.
#
# Formula:
#   sensor_kg = mean( (Px - ZERO[s][x]) / SENS[s][x]  for x in 0..3 )
#   total_kg  = sensor0 + sensor1 + sensor2 + sensor3
#
# Zero offsets ZERO[sensor][arm]: raw counts at 0 kg (live USB, 2026-04-15)
ZERO = {
    0: [-755.0, -2594.7,  1361.0, -3485.7],   # Sensor 0: P1, P2, P3, P4
    1: [-752.3, -2595.0,  1359.3, -3483.7],   # Sensor 1
    2: [-751.0, -2595.7,  1359.3, -3483.0],   # Sensor 2
    3: [-749.0, -2597.3,  1357.0, -3483.7],   # Sensor 3
}

# Sensitivity SENS[sensor][arm]: counts per kg of that sensor's load.
# Derived from 665 kg capture; each sensor carries 665/4 = 166.25 kg.
SENS = {
    0: [1.6186, 1.2491, 1.0482, 0.9479],   # Sensor 0: P1, P2, P3, P4
    1: [1.6026, 1.2511, 1.0585, 0.9360],   # Sensor 1
    2: [1.5947, 1.2555, 1.0586, 0.9314],   # Sensor 2
    3: [1.5825, 1.2652, 1.0723, 0.9357],   # Sensor 3
}

# NOTE: Calibration based on 665 kg single-point capture.
# Accurate above ~200 kg. Noise floor ≈ ±5 kg near zero.

def sensor_kg(frame, s):
    """
    Compute kg carried by sensor s.
    Averages the weight derived from each of the 4 bridge arms.
    """
    off = 1 + s * 11 + 3    # skip marker(1) + cfg(2), land on P1
    p1, p2, p3, p4 = struct.unpack_from('<4h', frame, off)
    arms = [p1, p2, p3, p4]
    kg_per_arm = [(arms[a] - ZERO[s][a]) / SENS[s][a] for a in range(4)]
    return sum(kg_per_arm) / 4.0

# ── Packet parser ─────────────────────────────────────────────────────────────

def decode_group(data, offset):
    """Return (marker, cfg, P1, P2, P3, P4) from group starting at offset."""
    marker = data[offset]
    cfg, p1, p2, p3, p4 = struct.unpack_from('<5h', data, offset + 1)
    return marker, cfg, p1, p2, p3, p4

def is_valid_packet(data):
    """Check 0x80 header and all 4 channel markers are in correct positions."""
    return (len(data) == PKT_SIZE and
            data[0]  == 0x80 and
            data[1]  == 0xC0 and
            data[12] == 0xC1 and
            data[23] == 0xC2 and
            data[34] == 0xC3)

# ── Rolling average ───────────────────────────────────────────────────────────

AVG_WINDOW    = 30
_avg_buf      = []
_display_init = False

def packet_to_kg(pkt):
    """Compute single-packet total weight: sum of all 4 sensors."""
    return sum(sensor_kg(pkt, s) for s in range(4))

def print_packet(pkt, pkt_no, show_raw):
    global _avg_buf, _display_init

    if show_raw:
        print("[{:06d}] RAW: {}".format(pkt_no, " ".join(f"{b:02x}" for b in pkt)))

    instant_kg = packet_to_kg(pkt)
    _avg_buf.append(instant_kg)
    if len(_avg_buf) > AVG_WINDOW:
        _avg_buf.pop(0)
    smoothed_kg = sum(_avg_buf) / len(_avg_buf)

    groups = [decode_group(pkt, 1 + ch * 11) for ch in range(4)]

    lines = []
    lines.append(f"  Packet #{pkt_no:<8d}  window: {len(_avg_buf)}/{AVG_WINDOW} packets")
    lines.append("─" * 62)
    lines.append(f"  {'Sensor':<6}  {'P1':>7}  {'P2':>7}  {'P3':>7}  {'P4':>7}  {'→ kg':>8}")
    for ch, (_, _, p1, p2, p3, p4) in enumerate(groups):
        s_kg = sensor_kg(pkt, ch)
        lines.append(f"  S{ch}     {p1:7d}  {p2:7d}  {p3:7d}  {p4:7d}  {s_kg:8.1f}")
    lines.append("─" * 62)
    lines.append(f"  Instant  : {instant_kg:+8.1f} kg")
    lines.append(f"  Smoothed : {smoothed_kg:+8.2f} kg  (avg {len(_avg_buf)} pkts)")
    lines.append("═" * 62)
    lines.append(f"  >>> WEIGHT: {smoothed_kg:8.2f} kg <<<")

    if _display_init:
        print(f"\x1b[{len(lines)}A", end="")
    for line in lines:
        print(f"\x1b[2K{line}")
    sys.stdout.flush()
    _display_init = True

# ── Serial sync ───────────────────────────────────────────────────────────────

def sync_and_read(ser, show_raw, quiet):
    buf           = bytearray()
    pkt_no        = 0
    sync_attempts = 0

    print(f"Listening on {ser.port} @ {ser.baudrate} baud ...")
    print("Waiting for packet sync (0x80 header) ...\n")

    while True:
        chunk = ser.read(PKT_SIZE)
        if not chunk:
            continue
        buf.extend(chunk)

        while len(buf) >= PKT_SIZE:
            idx = buf.find(0x80)
            if idx == -1:
                buf.clear()
                break
            if idx > 0:
                if not quiet:
                    print(f"  [sync] skipping {idx} byte(s) to reach 0x80", flush=True)
                del buf[:idx]
                sync_attempts += idx

            if len(buf) < PKT_SIZE:
                break

            candidate = bytes(buf[:PKT_SIZE])
            if is_valid_packet(candidate):
                pkt_no += 1
                if sync_attempts and pkt_no == 1:
                    print(f"  [sync] synced after skipping {sync_attempts} bytes\n")
                    sync_attempts = 0
                print_packet(candidate, pkt_no, show_raw)
                del buf[:PKT_SIZE]
            else:
                del buf[:1]

# ── Baud rate probe ───────────────────────────────────────────────────────────

def probe_baud(port, bauds, timeout=3):
    for baud in bauds:
        print(f"  Trying {baud} baud ...", end=" ", flush=True)
        try:
            with serial.Serial(port, baud, timeout=0.5) as ser:
                ser.reset_input_buffer()
                buf      = bytearray()
                deadline = time.time() + timeout
                while time.time() < deadline:
                    buf.extend(ser.read(PKT_SIZE * 4))
                    for i in range(len(buf) - PKT_SIZE + 1):
                        if buf[i] == 0x80 and is_valid_packet(bytes(buf[i:i+PKT_SIZE])):
                            print(f"VALID PACKET FOUND → using {baud} baud")
                            return baud
                print("no valid packet")
        except serial.SerialException as e:
            print(f"error: {e}")
    return None

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Read weight packets from weighing sensor via USB serial")
    parser.add_argument("--port",  default=PORT,
                        help=f"Serial port (default: {PORT})")
    parser.add_argument("--baud",  type=int, nargs="+", default=[BAUD],
                        help=f"Baud rate(s) to try (default: {BAUD}). Multiple = auto-probe.")
    parser.add_argument("--raw",   action="store_true",
                        help="Also print raw hex bytes for each packet")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress sync messages")
    args = parser.parse_args()

    baud = args.baud[0]
    if len(args.baud) > 1:
        print(f"Auto-probing baud rate on {args.port} ...")
        found = probe_baud(args.port, args.baud)
        if found:
            baud = found
        else:
            print("Could not find a valid baud rate. Check wiring or try --baud manually.")
            sys.exit(1)

    try:
        ser = serial.Serial(
            port     = args.port,
            baudrate = baud,
            bytesize = serial.EIGHTBITS,
            parity   = serial.PARITY_NONE,
            stopbits = serial.STOPBITS_ONE,
            timeout  = 2,
        )
    except serial.SerialException as e:
        print(f"Cannot open {args.port}: {e}")
        print("Try:  sudo usermod -aG dialout $USER  (then log out/in)")
        sys.exit(1)

    try:
        sync_and_read(ser, show_raw=args.raw, quiet=args.quiet)
    except KeyboardInterrupt:
        print("\n\nStopped.")
    finally:
        ser.close()

if __name__ == "__main__":
    main()
