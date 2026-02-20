#!/usr/bin/env python3
"""
CPU Stressor for Thesis Experiments
======================================
Runs CPU-spinning shell loops inside each webapp pod via kubectl exec.
One subprocess per worker × pod — each burns 100% of one core.

Usage:
    # 2 workers per pod, run for 5 minutes
    python3 cpu_stressor.py --workers 2 --duration 300

    # Aggressive: 4 workers per pod for 2 minutes
    python3 cpu_stressor.py --workers 4 --duration 120

    # Only stress 1 pod (useful for partial load)
    python3 cpu_stressor.py --workers 2 --duration 120 --max-pods 1
"""

import argparse
import logging
import signal
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

NAMESPACE = "default"
LABEL_SELECTOR = "app.kubernetes.io/name=webapp"
CONTAINER = "nginx"

# Pure shell busy-loop that self-terminates after SECONDS seconds.
# Using date arithmetic avoids needing the `timeout` binary (not in nginx image).
# This ensures the stress stops inside the container even if kubectl exec is killed.
CPU_STRESS_CMD = "end=$(( $(date +%s) + {seconds} )); while [ $(date +%s) -lt $end ]; do :; done"


def get_pods() -> list[str]:
    result = subprocess.run(
        [
            "kubectl", "get", "pods",
            "-n", NAMESPACE,
            "-l", LABEL_SELECTOR,
            "-o", "jsonpath={.items[*].metadata.name}",
        ],
        capture_output=True, text=True, check=True,
    )
    names = result.stdout.strip().split()
    if not names or names == [""]:
        raise RuntimeError(f"No pods found with selector '{LABEL_SELECTOR}'")
    return names


def start_worker(pod: str, worker_id: int, duration_s: int) -> subprocess.Popen:
    """Start one CPU-spinning process inside a pod container."""
    cmd = CPU_STRESS_CMD.format(seconds=duration_s)
    proc = subprocess.Popen(
        [
            "kubectl", "exec", pod,
            "-n", NAMESPACE,
            "-c", CONTAINER,
            "--", "sh", "-c", cmd,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.debug("Started worker %d on %s (pid=%d, duration=%ds)", worker_id, pod, proc.pid, duration_s)
    return proc


def main() -> None:
    parser = argparse.ArgumentParser(description="CPU stressor via kubectl exec")
    parser.add_argument("--workers", type=int, default=2,
                        help="CPU worker loops per pod (default: 2)")
    parser.add_argument("--duration", type=float, default=300.0,
                        help="How long to run in seconds (default: 300, 0=infinite)")
    parser.add_argument("--max-pods", type=int, default=None,
                        help="Limit to this many pods (default: all)")
    parser.add_argument("--refresh", type=float, default=15.0,
                        help="Re-query pods every N seconds to stress newly scaled pods (default: 15)")
    args = parser.parse_args()

    end_time = time.monotonic() + args.duration if args.duration else None
    remaining_s = int(args.duration) if args.duration else 86400  # cap at 24h if infinite

    logger.info(
        "CPU stressor starting | workers=%d/pod  duration=%ss  refresh=%ss",
        args.workers,
        f"{int(args.duration)}" if args.duration else "∞",
        int(args.refresh),
    )

    # pod_name → list of worker procs
    active: dict[str, list[subprocess.Popen]] = {}
    worker_id = 0
    start_time = time.monotonic()

    try:
        while True:
            if end_time and time.monotonic() >= end_time:
                logger.info("Duration reached — stopping")
                break

            # Discover current pods, filter to max-pods if set
            try:
                current_pods = get_pods()
            except RuntimeError as exc:
                logger.warning("Pod query failed: %s — retrying next cycle", exc)
                time.sleep(args.refresh)
                continue

            if args.max_pods:
                current_pods = current_pods[: args.max_pods]

            # Start workers on any pod not yet stressed
            new_pods = [p for p in current_pods if p not in active]
            for pod in new_pods:
                elapsed = time.monotonic() - start_time
                secs_left = max(1, int(remaining_s - elapsed))
                workers = []
                for _ in range(args.workers):
                    worker_id += 1
                    workers.append(start_worker(pod, worker_id, secs_left))
                active[pod] = workers
                logger.info("Stressing pod %s with %d worker(s) for %ds",
                            pod, args.workers, secs_left)

            # Report
            total_alive = sum(
                1 for procs in active.values() for p in procs if p.poll() is None
            )
            logger.info(
                "t=%ds  pods=%d  workers alive=%d",
                int(time.monotonic() - start_time), len(active), total_alive,
            )

            time.sleep(args.refresh)

    except KeyboardInterrupt:
        logger.info("Interrupted — stopping workers")

    finally:
        all_procs = [p for procs in active.values() for p in procs]
        for p in all_procs:
            if p.poll() is None:
                p.terminate()
        # Give kubectl exec a moment to propagate the kill
        time.sleep(1)
        for p in all_procs:
            if p.poll() is None:
                p.kill()
        logger.info("All workers stopped")


if __name__ == "__main__":
    main()
