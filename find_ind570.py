#!/usr/bin/env python3
"""
find_ind570.py — discover the IND570's IP on the local subnet.

Standalone diagnostic tool, NOT part of the metrologically-relevant app
(basic_interface_ind570.py). Run this manually whenever the IND570's IP
is unknown or suspected to have changed; it does not modify anything on
the network or on the IND570 itself — it only opens short-lived TCP probe
connections to see what answers.

Usage:
    python3 find_ind570.py                        # scan default subnet, port 1702
    python3 find_ind570.py --subnet 192.168.1.0/24 --port 1702
    python3 find_ind570.py --iface enx00e04c235970 # bind scan to one interface

Requires only the standard library — no extra dependencies.
"""

import argparse
import ipaddress
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed


def probe(ip: str, port: int, timeout: float, read_bytes: int = 64):
    """Try to TCP-connect to ip:port. Return (ip, sample_bytes_or_None, error_or_None)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((ip, port))
            # Connected — try a short non-blocking-ish read to see if the
            # far end is actively streaming (IND570 continuous-output mode
            # pushes data without waiting for a request).
            sock.settimeout(1.0)
            try:
                data = sock.recv(read_bytes)
            except socket.timeout:
                data = b''
            return (ip, data, None)
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return (ip, None, str(e))


def main():
    ap = argparse.ArgumentParser(description="Scan a subnet for a device answering on the IND570's TCP port.")
    ap.add_argument('--subnet', default='192.168.0.0/24',
                     help='CIDR subnet to scan (default: 192.168.0.0/24)')
    ap.add_argument('--port', type=int, default=1702,
                     help='TCP port to probe (default: 1702, the IND570 default in this app)')
    ap.add_argument('--timeout', type=float, default=0.3,
                     help='Per-host connect timeout in seconds (default: 0.3)')
    ap.add_argument('--workers', type=int, default=64,
                     help='Parallel probe threads (default: 64)')
    args = ap.parse_args()

    try:
        network = ipaddress.ip_network(args.subnet, strict=False)
    except ValueError as e:
        print(f"Invalid subnet {args.subnet!r}: {e}", file=sys.stderr)
        sys.exit(1)

    hosts = list(network.hosts())
    print(f"Scanning {len(hosts)} addresses in {network} on port {args.port} "
          f"(timeout {args.timeout}s, {args.workers} workers)...")

    found = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, str(ip), args.port, args.timeout): ip for ip in hosts}
        done = 0
        for fut in as_completed(futures):
            done += 1
            ip, data, err = fut.result()
            if err is None:
                found.append((ip, data))
                tag = "streaming data" if data else "connected, silent so far"
                print(f"  [OPEN] {ip}:{args.port} — {tag}")
                if data:
                    print(f"         sample: {data!r}")
            if done % 64 == 0:
                print(f"  ...{done}/{len(hosts)} scanned", file=sys.stderr)

    print()
    if not found:
        print(f"No device answered on port {args.port} anywhere in {network}.")
        print("This means either:")
        print("  - the IND570 is not on this subnet (wrong cable/adapter/segment), or")
        print("  - its Ethernet/TCP-IP interface is not active (e.g. set to USB mode), or")
        print("  - continuous-output-over-TCP is not enabled in its setup menu.")
        sys.exit(2)
    else:
        print(f"Found {len(found)} device(s) answering on port {args.port}:")
        for ip, data in found:
            print(f"  {ip}" + ("  <- looks like it's actively streaming" if data else ""))
        print()
        print("If exactly one device streamed data automatically, that's almost")
        print("certainly the IND570 — update IND570_HOST in basic_interface_ind570.py")
        print("to that address (and rebuild if you distribute the .exe).")


if __name__ == '__main__':
    main()
