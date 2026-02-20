#!/usr/bin/env python3
"""
HTTP Load Generator for Thesis Experiments
===========================================
Sends concurrent HTTP requests to the webapp endpoint to drive CPU load
so the HPA and AI scaler have something to react to.

On Docker Desktop, NodePorts are not exposed to the host. The script
automatically opens a kubectl port-forward to the webapp deployment
on port 18080 and tears it down on exit.

Usage:
    # Low load (warm-up / baseline)
    python3 load_generator.py --rps 5 --duration 60

    # Medium load (trigger HPA scale-up)
    python3 load_generator.py --rps 50 --duration 300

    # High load spike
    python3 load_generator.py --rps 200 --duration 120

    # Ramp: 10 → 200 rps over 5 minutes, hold 2 min, then stop
    python3 load_generator.py --ramp-start 10 --ramp-end 200 --ramp-duration 300 --hold 120

    # Use an already-running URL (skip auto port-forward)
    python3 load_generator.py --url http://localhost:18080/ --rps 50 --duration 300
"""

import argparse
import asyncio
import logging
import signal
import socket
import subprocess
import time

try:
    import aiohttp
except ImportError:
    raise SystemExit("aiohttp not installed – run: pip install aiohttp")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Docker Desktop's LoadBalancer proxy on port 8080 is intercepted by its own
# internal Go server. Use kubectl port-forward to bypass it reliably.
LOCAL_PORT = 18080
TARGET_URL = f"http://localhost:{LOCAL_PORT}/"
NAMESPACE = "default"
SVC_NAME = "webapp-service"
SVC_PORT = 8080   # service port (maps to pod port 80)
REPORT_INTERVAL_S = 10   # print stats every N seconds


def start_port_forward() -> subprocess.Popen:
    """Start kubectl port-forward and wait until the port is open."""
    cmd = [
        "kubectl", "port-forward",
        f"svc/{SVC_NAME}",
        f"{LOCAL_PORT}:{SVC_PORT}",
        "-n", NAMESPACE,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Wait up to 5s for the port to open
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", LOCAL_PORT), timeout=0.3):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError(
            f"kubectl port-forward to {DEPLOYMENT}:{POD_PORT} did not open "
            f"localhost:{LOCAL_PORT} within 5 seconds"
        )
    logger.info("Port-forward ready: localhost:%d → svc/%s:%d", LOCAL_PORT, SVC_NAME, SVC_PORT)
    return proc


async def _worker(session: aiohttp.ClientSession, url: str, counters: dict) -> None:
    """Single fire-and-forget request."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            counters["total"] += 1
            if resp.status < 400:
                counters["ok"] += 1
            else:
                counters["err"] += 1
    except Exception:
        counters["total"] += 1
        counters["err"] += 1


async def run(
    url: str,
    rps: float,
    duration: float,
    ramp_start: float | None = None,
    ramp_end: float | None = None,
    ramp_duration: float | None = None,
    hold: float = 0.0,
) -> None:
    """Drive load at the target RPS (optionally ramping)."""
    connector = aiohttp.TCPConnector(limit=512)
    counters: dict = {"total": 0, "ok": 0, "err": 0}
    pending: set = set()

    stop = False

    def _handle_stop(sig, frame):
        nonlocal stop
        logger.info("Caught signal – stopping after current requests finish")
        stop = True

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    async with aiohttp.ClientSession(connector=connector) as session:
        start = time.monotonic()
        last_report = start
        report_counters = {"total": 0, "ok": 0, "err": 0}
        requests_sent = 0

        total_duration = duration + hold

        while not stop:
            now = time.monotonic()
            elapsed = now - start

            if elapsed >= total_duration:
                break

            # Compute current target RPS
            if ramp_start is not None and ramp_end is not None and ramp_duration:
                ramp_elapsed = min(elapsed, ramp_duration)
                current_rps = ramp_start + (ramp_end - ramp_start) * (ramp_elapsed / ramp_duration)
            else:
                current_rps = rps

            # How many requests should have been sent by now?
            expected = int(current_rps * elapsed)
            deficit = expected - requests_sent
            for _ in range(max(0, deficit)):
                task = asyncio.create_task(_worker(session, url, counters))
                pending.add(task)
                task.add_done_callback(pending.discard)
                requests_sent += 1

            # Periodic report
            if now - last_report >= REPORT_INTERVAL_S:
                window = now - last_report
                new_total = counters["total"] - report_counters["total"]
                new_ok    = counters["ok"]    - report_counters["ok"]
                new_err   = counters["err"]   - report_counters["err"]
                actual_rps = new_total / window if window > 0 else 0
                logger.info(
                    "t=%ds  target=%.0f rps  actual=%.1f rps  ok=%d  err=%d  in-flight=%d",
                    int(elapsed), current_rps, actual_rps, new_ok, new_err, len(pending),
                )
                report_counters = dict(counters)
                last_report = now

            await asyncio.sleep(0.001)  # yield to event loop

        # Wait for in-flight requests to complete
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    logger.info(
        "Done. total=%d  ok=%d  err=%d  duration=%.1fs",
        counters["total"], counters["ok"], counters["err"], time.monotonic() - start,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="HTTP load generator for webapp")
    parser.add_argument("--url", default=TARGET_URL, help="Target URL")
    parser.add_argument("--rps", type=float, default=50.0, help="Requests per second (constant)")
    parser.add_argument("--duration", type=float, default=300.0, help="Total load duration (seconds)")
    parser.add_argument("--hold", type=float, default=0.0, help="Extra seconds to hold at peak after ramp")
    parser.add_argument("--ramp-start", type=float, default=None, help="Starting RPS for ramp")
    parser.add_argument("--ramp-end", type=float, default=None, help="Ending RPS for ramp")
    parser.add_argument("--ramp-duration", type=float, default=None, help="Duration of ramp phase (seconds)")
    args = parser.parse_args()

    pf_proc = None
    if args.url == TARGET_URL:
        try:
            pf_proc = start_port_forward()
        except RuntimeError as exc:
            raise SystemExit(f"Port-forward failed: {exc}")

    logger.info(
        "Starting load generator → %s  rps=%.0f  duration=%ds",
        args.url, args.rps, int(args.duration),
    )

    try:
        asyncio.run(run(
            url=args.url,
            rps=args.rps,
            duration=args.duration,
            ramp_start=args.ramp_start,
            ramp_end=args.ramp_end,
            ramp_duration=args.ramp_duration,
            hold=args.hold,
        ))
    finally:
        if pf_proc is not None:
            pf_proc.terminate()
            logger.info("Port-forward stopped")


if __name__ == "__main__":
    main()
