# Ethernet Data Transmission — IND570 → basic_interface_ind570.py

How weight data gets from the IND570 into the app. Based on what the code actually does — the IND570-side setup (SICS/continuous-output config) isn't something this repo controls, check the IND570 manual for that part.

---

## Physical / link layer

```
Mettler-Toledo load cell → (analog) → IND570 terminal → (Ethernet) → host PC
```

Load cell wiring into the IND570 is the IND570's own business — out of scope here. IND570 and the host PC need to be on the same subnet, since the app connects to a hardcoded IP, no discovery.

---

## Network config (hardcoded)

```python
IND570_HOST = "192.168.0.1"
IND570_PORT = 1702
```

If the IND570's actual IP/port differs, the app won't connect. Changing this means editing the source and rebuilding — no runtime config option currently (the COM-port dropdown in the UI isn't wired to this, see SYSTEM_OVERVIEW.md).

---

## Transport — TCP client

App is the client, IND570 is the server. `reader_thread()`:

1. Opens a TCP socket, 3s timeout
2. Connects to `(IND570_HOST, IND570_PORT)`
3. Loops on `sock.recv(256)`

IND570 needs to already be streaming weight data on that port (continuous output mode) — that's IND570-side config, not something the app sets up.

---

## Framing

No packet headers, just a byte stream. App reconstructs lines itself:

```python
buf += chunk
while '\n' in buf:
    line, buf = buf.split('\n', 1)
    kg = _parse_line(line)
```

Decoded as ASCII, bad bytes dropped. Buffers partial lines across reads so a line split across two `recv()` calls still works. IND570 needs to terminate each reading with `\n` (or `\r\n`) for this to work.

---

## Parsing

```python
_LINE_RE = re.compile(r'([+-]?\d+\.?\d*)\s*(kg|g|lb|t)', re.IGNORECASE)
```

Searches the line for number + unit, doesn't need to match the whole line — so `"      6.10 kg \r"` works fine with junk around it.

Unit → kg conversion:

| Unit | Conversion |
|---|---|
| kg | none |
| g | ÷1000 |
| lb | ×0.453592 |
| t | ×1000 |

Lines that don't match get silently dropped — not an error, just ignored.

---

## Reconnect

```python
except Exception:
    state.set_error()
finally:
    sock.close()
if running.is_set():
    threading.Event().wait(2)
```

Any failure → red error banner in the UI, socket closed, retry in 2s. Runs forever while the app is running. No manual reconnect needed after a network blip or IND570 reboot.

---

## Byte counter

`bytes_rx` — raw byte count on the current connection, shown in the status bar. Just confirms data is flowing, doesn't tell you how many lines actually parsed successfully.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Red "ERROR: Cannot connect" | `ping 192.168.0.1`, `Test-NetConnection 192.168.0.1 -Port 1702` |
| Connects, display frozen | IND570 sending wrong format/unit or no `\n` — capture the raw stream and check against the regex above |
| Bytes climbing, value frozen | Same as above — lines arriving but not matching the parser |
| Drops every few minutes | Network/IND570 resetting the connection — reconnect is automatic, but check network stability if it's frequent |
