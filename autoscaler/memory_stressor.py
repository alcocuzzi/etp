#!/usr/bin/env python3
"""
Memory Stressor for Thesis Experiments
========================================
Allocates a fixed block of memory inside each webapp pod via kubectl exec
by writing zeros to /dev/shm (tmpfs — counts as container memory usage).
Holds the allocation for the experiment duration, then cleans up.

NOTE: /dev/shm inside the nginx container is 64MB (default Kubernetes shmSize).
The pod memory request is 64Mi, so writing 50MB puts utilization at ~78% which
is enough to breach the 80% HPA memory threshold.

Usage:
    # Allocate 50 MB per pod for 5 minutes (triggers HPA ~78% util)
    python3 memory_stressor.py --mb 50 --duration 300

    # Hold longer to observe scale-down after release
    python3 memory_stressor.py --mb 50 --duration 120

    # Only stress 1 pod
    python3 memory_stressor.py --mb 50 --duration 120 --max-pods 1
"""

import argparse
import logging
import subprocess
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
STRESS_FILE = "/dev/shm/mem_stress_load"   # tmpfs — counted as container memory

# Allocate N MB of zeros to /dev/shm to hold whilst running, clean up on exit.
# The trap ensures the file is removed even if kubectl exec is terminated.
MEMORY_STRESS_CMD = (
    "trap 'rm -f {path}' EXIT INT TERM; "
    "dd if=/dev/zero of={path} bs=1M count={mb} 2>/dev/null && "
    "echo 'allocated {mb}MB' && "
    "sleep 99999"
)


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


def cleanup_pod(pod: str) -> None:
    """Remove the stress file from the pod (best-effort)."""
    subprocess.run(
        [
            "kubectl", "exec", pod,
            "-n", NAMESPACE,
            "-c", CONTAINER,
            "--", "rm", "-f", STRESS_FILE,
        ],
        capture_output=True,
    )


def check_shm_space(pod: str, mb: int) -> None:
    """Warn if /dev/shm doesn't have enough free space."""
    result = subprocess.run(
        [
            "kubectl", "exec", pod, "-n", NAMESPACE, "-c", CONTAINER,
            "--", "df", "-k", "/dev/shm",
        ],
        capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        if "/dev/shm" in line:
            parts = line.split()
            available_mb = int(parts[3]) // 1024
            if mb > available_mb:
                raise RuntimeError(
                    f"{pod}: /dev/shm only has {available_mb}MB free but you requested {mb}MB. "
                    f"Lower --mb to {available_mb - 5} or less."
                )
            logger.debug("%s: /dev/shm has %dMB free, allocating %dMB", pod, available_mb, mb)
            return


def start_allocation(pod: str, mb: int) -> subprocess.Popen:
    cmd = MEMORY_STRESS_CMD.format(path=STRESS_FILE, mb=mb)
    proc = subprocess.Popen(
        [
            "kubectl", "exec", pod,
            "-n", NAMESPACE,
            "-c", CONTAINER,
            "--", "sh", "-c", cmd,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    # Wait briefly for allocation to complete
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if proc.stdout and proc.stdout.readable():
            line = proc.stdout.readline()
            if "allocated" in line:
                logger.info("%-45s  allocated %dMB to %s", pod, mb, STRESS_FILE)
                return proc
        if proc.poll() is not None:
            raise RuntimeError(f"Allocation process on {pod} exited early (code={proc.returncode})")
        time.sleep(0.2)
    logger.warning("Allocation confirmation not received for %s — process may still be running", pod)
    return proc


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory stressor via kubectl exec + /dev/shm")
    parser.add_argument("--mb", type=int, default=50,
                        help="MB to allocate per pod in /dev/shm (default: 50; max ~60 with default shmSize)")
    parser.add_argument("--duration", type=float, default=300.0,
                        help="How long to hold the allocation in seconds (default: 300, 0=infinite)")
    parser.add_argument("--max-pods", type=int, default=None,
                        help="Limit to this many pods (default: all)")
    args = parser.parse_args()

    pods = get_pods()
    if args.max_pods:
        pods = pods[: args.max_pods]

    logger.info("Targeting %d pod(s): %s", len(pods), ", ".join(pods))
    logger.info(
        "Allocating %dMB per pod (%dMB total) | duration=%ss",
        args.mb, args.mb * len(pods),
        f"{int(args.duration)}" if args.duration else "∞",
    )

    procs: list[tuple[str, subprocess.Popen]] = []   # (pod_name, proc)
    try:
        for pod in pods:
            check_shm_space(pod, args.mb)
            proc = start_allocation(pod, args.mb)
            procs.append((pod, proc))

        logger.info("Memory allocated on all pods — press Ctrl+C to release early")

        end = time.monotonic() + args.duration if args.duration else None
        while True:
            if end and time.monotonic() >= end:
                logger.info("Duration reached — releasing memory")
                break
            alive = sum(1 for _, p in procs if p.poll() is None)
            logger.info("t=%ds  allocations alive=%d/%d", int(time.monotonic()), alive, len(procs))
            time.sleep(10)

    except KeyboardInterrupt:
        logger.info("Interrupted — releasing memory")

    finally:
        for pod, p in procs:
            if p.poll() is None:
                p.terminate()
        time.sleep(1)
        # Belt-and-braces: also delete the file directly in case the process
        # didn't honour the trap (kubectl exec termination can race)
        for pod, _ in procs:
            cleanup_pod(pod)
            logger.debug("Cleaned up %s on %s", STRESS_FILE, pod)
        logger.info("Memory stress released on all pods")


if __name__ == "__main__":
    main()
