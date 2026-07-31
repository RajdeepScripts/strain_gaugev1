# Ethernet Data Transmission — IND570 → basic_interface_ind570.py

**ADJ Engineering Pvt. Ltd.**
Describes exactly how weight data reaches the application over the network, based on what `basic_interface_ind570.py` actually implements. Sections marked **[not verified from code]** describe the IND570 side of the link, which this codebase does not configure — verify those against the IND570 manual/setup menu before relying on them.

---

## 1. Physical / link layer

```
Mettler-Toledo load cell  ──(analog signal, load cell cable)──▶  IND570 terminal
IND570 terminal           ──(Ethernet cable)──▶  Network switch/router  ──▶  Host PC running the app
```

- The load cell connects to the IND570 by cable (analog excitation/signal wiring) — this link is internal to the IND570's own setup and is out of scope for the application; the IND570 handles ADC and produces a weight reading internally.
- The IND570 exposes an Ethernet port. It and the host PC running `basic_interface_ind570.py` must be on the same IP subnet (either directly connected, through a switch, or a LAN), since the app connects to the IND570's IP directly rather than through a hostname/discovery mechanism.

---

## 2. Network layer configuration used by the app

Hardcoded in `basic_interface_ind570.py:22-23`:

```python
IND570_HOST = "192.168.0.1"
IND570_PORT = 1702
```

| Parameter | Value in code | Notes |
|---|---|---|
| Terminal IP | `192.168.0.1` | The app connects to this IP. If the IND570 is actually configured to a different address, the app will fail to connect (see §6). |
| Port | `1702` | TCP port the app connects to. |
| Host PC IP | Not set by this app | Whatever the Windows machine's Ethernet adapter is configured with — must be on the same `/24` (or otherwise routable) as `192.168.0.1`. |

Changing the target IP/port requires editing these two lines and rebuilding the executable — there is currently no runtime/config-file way to change them (the COM-port dropdown in the UI is not wired to this; see `SYSTEM_OVERVIEW.md` §4.1 and §9).

---

## 3. Transport layer — TCP client

The app is a **TCP client**, not a server. `reader_thread()` (basic_interface_ind570.py:86-113):

1. Opens a standard IPv4/TCP socket: `socket.socket(socket.AF_INET, socket.SOCK_STREAM)`
2. Sets a 3-second connect/recv timeout: `sock.settimeout(3)`
3. Connects to `(IND570_HOST, IND570_PORT)`
4. Enters a read loop calling `sock.recv(256)` — reads up to 256 bytes at a time, however much is available; TCP gives no guarantee this aligns with a single line of data, which is why framing is handled separately (§4)

The IND570 must already be configured to act as the **TCP server** on port 1702 and stream weight data to any connected client — this is the IND570's continuous-output-over-Ethernet mode. **[not verified from code]** Exact IND570 setup-menu steps to enable this (SICS/continuous output mode, port assignment) are not something this codebase configures or documents; consult the IND570 manual/your instrument's setup for that side.

---

## 4. Application-layer framing

The IND570 stream has no explicit packet/length header — it's a continuous byte stream. The app reconstructs lines itself:

```python
buf = ''
...
chunk = sock.recv(256).decode('ascii', errors='ignore')
buf += chunk
while '\n' in buf:
    line, buf = buf.split('\n', 1)
    kg = _parse_line(line)
    ...
```

- Bytes are decoded as **ASCII**, with undecodable bytes silently dropped (`errors='ignore'`)
- Decoded text accumulates in `buf` until a newline (`\n`) is found
- Each complete line (everything before the `\n`) is popped off and parsed independently; leftover partial text after the last `\n` stays in `buf` for the next `recv()` — so a line split across two TCP reads is handled correctly
- Carriage returns (`\r`), if present before `\n`, are not explicitly stripped, but the parsing regex (§5) only searches for a number+unit pattern within the line, so a trailing `\r` doesn't break matching

This means the IND570 must terminate each weight reading with `\n` (or `\r\n`) for the framing to work — a stream that used a different terminator or fixed-width binary records would not parse correctly with this code.

---

## 5. Payload format and parsing

Expected line shape, based on the parsing regex (basic_interface_ind570.py:27):

```python
_LINE_RE = re.compile(r'([+-]?\d+\.?\d*)\s*(kg|g|lb|t)', re.IGNORECASE)
```

- Matches an optional sign, digits, optional decimal point and more digits, optional whitespace, then a unit token: `kg`, `g`, `lb`, or `t` (case-insensitive)
- The regex **searches** the line rather than anchoring to the start, so surrounding text/whitespace/other fields on the same line are tolerated as long as one number+unit pair appears
- Example line the IND570 might send: `"      6.10 kg \r"` — matches `6.10` + `kg`

### 5.1 Unit normalization

`_parse_line()` (basic_interface_ind570.py:30-42) converts every value to **kilograms** before it reaches the rest of the app:

| Unit matched | Conversion applied |
|---|---|
| `kg` | none (used as-is) |
| `g` | `value / 1000.0` |
| `lb` | `value * 0.453592` |
| `t` | `value * 1000.0` |

If a line doesn't match the regex at all (e.g., a header/status line from the IND570, or noise), `_parse_line` returns `None` and that line is silently discarded — it does not count as an error and does not affect the connection state.

---

## 6. Reliability and reconnection

```python
except Exception:
    state.set_error()
finally:
    sock.close()
if running.is_set():
    threading.Event().wait(2)
```

- Any exception during connect/recv (timeout, connection refused, network unreachable, host down) is caught, marks `SharedState.valid = -1` (surfaces as the red "ERROR: Cannot connect" banner in the UI), and closes the socket
- After an error (or a clean disconnect where `recv()` returns empty), the outer `while running.is_set()` loop waits 2 seconds, then tries to reconnect from scratch
- This repeats indefinitely as long as the app is running (`_running` event set) — no user action is needed to recover from a transient network drop or an IND570 reboot

---

## 7. Byte/throughput accounting

`bytes_rx` is a running total of raw bytes received on the current connection (reset to 0 on each new connection attempt), shown in the UI status bar as "Bytes on port: N". This is a simple diagnostic counter — it confirms data is flowing but does not indicate the number of valid parsed readings versus discarded/malformed lines.

---

## 8. Summary diagram

```
┌────────────────────┐   analog    ┌─────────────┐  ASCII lines, \n-terminated   ┌────────────────────────────┐
│ Mettler-Toledo      │────signal──▶│  IND570      │───────over TCP:1702─────────▶│ basic_interface_ind570.py  │
│ load cell           │             │  terminal    │   (IND570 = TCP server,       │  reader_thread():          │
└────────────────────┘             │              │    app = TCP client)          │   recv(256) → buffer →     │
                                     └─────────────┘                               │   split on \n → regex      │
                                                                                    │   parse → normalize to kg  │
                                                                                    │   → SharedState.push()     │
                                                                                    └────────────────────────────┘
```

## 9. Troubleshooting checklist

| Symptom | Likely cause | Check |
|---|---|---|
| Red "ERROR: Cannot connect to 192.168.0.1:1702" | Wrong IP/port, IND570 not in continuous-output mode, cable/switch issue, firewall | `ping 192.168.0.1`; `Test-NetConnection 192.168.0.1 -Port 1702` (PowerShell) |
| Connects but display never updates | IND570 sending a format the regex doesn't match (wrong unit token, no newline, different decimal format) | Capture raw stream (e.g., `nc 192.168.0.1 1702` from a Linux box on the same network) and compare against §5 pattern |
| Bytes received climbing but value frozen | Lines are arriving but not matching `_LINE_RE` — likely a unit/format mismatch | Same as above; check actual line content against the regex |
| Works, then drops every few minutes | IND570 or network intermittently resetting the TCP connection | Reconnect is automatic (§6); if drops are frequent, check network stability/IND570 Ethernet settings, not the app |
