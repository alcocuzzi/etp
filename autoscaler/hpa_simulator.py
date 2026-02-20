"""
Conventional HPA Simulator
===========================
Replicates the Kubernetes HPA algorithm so we can compute what the HPA *would*
recommend at any point in time, even when the AI scaler is in charge.

This lets us produce side-by-side comparison data for the thesis.

Reference logic:
  desiredReplicas = ceil(currentReplicas * (currentMetricValue / desiredMetricValue))

See: https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
"""

import logging
import math

from config import KubernetesConfig

logger = logging.getLogger(__name__)

# HPA stabilisation windows (seconds) – same defaults as Kubernetes
SCALE_UP_STABILIZATION_WINDOW_S = 0     # K8s default: 0 (immediate)
SCALE_DOWN_STABILIZATION_WINDOW_S = 60   # Reduced to 60s to match experiment HPA


class HPASimulator:
    """
    Stateful simulator of the Kubernetes HPA algorithm.

    Call ``compute(...)`` on every iteration; the simulator keeps an internal
    history to model the stabilisation window correctly.
    """

    def __init__(
        self,
        k8s_cfg: KubernetesConfig,
        cpu_target_utilization: float | None = None,
        memory_target_utilization: float | None = None,
    ):
        self.k8s_cfg = k8s_cfg
        # Fall back to values from KubernetesConfig (which mirrors hpa.yaml)
        self.cpu_target_utilization = cpu_target_utilization if cpu_target_utilization is not None else k8s_cfg.cpu_target_utilization
        self.memory_target_utilization = memory_target_utilization if memory_target_utilization is not None else k8s_cfg.memory_target_utilization

        # History for stabilisation window: list of (timestamp_s, desired_replicas)
        self._scale_up_history: list[tuple[float, int]] = []
        self._scale_down_history: list[tuple[float, int]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(
        self,
        current_replicas: int,
        cpu_millicores: float,
        memory_mebibytes: float,
        cpu_request_millicores: float,
        memory_request_mebibytes: float,
        timestamp_s: float,
    ) -> dict:
        """
        Compute the HPA's scaling decision given current metrics.

        All CPU values in millicores, all memory values in MiB.
        Inputs are per-pod averages (matching the `avg(...)` PromQL queries).

        Returns a dict with:
            hpa_sim_desired       – replicas after stabilisation window
            hpa_sim_raw_desired   – replicas before stabilisation window
            hpa_sim_cpu_desired   – replica count driven by CPU metric
            hpa_sim_mem_desired   – replica count driven by memory metric (if enabled)
            hpa_sim_action        – "scale_up" | "scale_down" | "no_change"
        """
        current_replicas = max(1, current_replicas)

        # --- CPU-based desired replicas -----------------------------------
        # ratio = actual_per_pod / (request_per_pod * target_utilization)
        cpu_target = cpu_request_millicores * self.cpu_target_utilization
        cpu_ratio = cpu_millicores / cpu_target if cpu_target > 0 else 1.0
        cpu_desired = math.ceil(current_replicas * cpu_ratio)

        # --- Memory-based desired replicas --------------------------------
        mem_target = memory_request_mebibytes * self.memory_target_utilization
        mem_ratio = memory_mebibytes / mem_target if mem_target > 0 else 1.0
        mem_desired = math.ceil(current_replicas * mem_ratio)

        # --- Take the maximum of all metric suggestions ------------------
        raw_desired = max(cpu_desired, mem_desired)
        raw_desired = max(self.k8s_cfg.min_replicas,
                          min(self.k8s_cfg.max_replicas, raw_desired))

        # --- Stabilisation window logic ----------------------------------
        if raw_desired > current_replicas:
            self._scale_up_history.append((timestamp_s, raw_desired))
            # For scale-up, use the maximum desired within the window
            self._prune_history(self._scale_up_history,
                                 timestamp_s, SCALE_UP_STABILIZATION_WINDOW_S)
            stabilised = max(d for _, d in self._scale_up_history)
        elif raw_desired < current_replicas:
            self._scale_down_history.append((timestamp_s, raw_desired))
            # For scale-down, use the maximum desired within the window
            # (conservative: only scale down if sustained for the full window)
            self._prune_history(self._scale_down_history,
                                 timestamp_s, SCALE_DOWN_STABILIZATION_WINDOW_S)
            stabilised = max(d for _, d in self._scale_down_history)
        else:
            stabilised = current_replicas

        stabilised = max(self.k8s_cfg.min_replicas,
                         min(self.k8s_cfg.max_replicas, stabilised))

        if stabilised > current_replicas:
            action = "scale_up"
        elif stabilised < current_replicas:
            action = "scale_down"
        else:
            action = "no_change"

        logger.debug(
            "HPA sim: cpu_desired=%d mem_desired=%d raw=%d stabilised=%d action=%s",
            cpu_desired, mem_desired, raw_desired, stabilised, action,
        )

        return {
            "hpa_sim_desired": stabilised,
            "hpa_sim_raw_desired": raw_desired,
            "hpa_sim_cpu_desired": cpu_desired,
            "hpa_sim_mem_desired": mem_desired,
            "hpa_sim_action": action,
            "hpa_sim_cpu_ratio": round(cpu_ratio, 4),
            "hpa_sim_mem_ratio": round(mem_ratio, 4),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _prune_history(
        self,
        history: list[tuple[float, int]],
        now: float,
        window_s: float,
    ) -> None:
        cutoff = now - window_s
        to_remove = [entry for entry in history if entry[0] < cutoff]
        for entry in to_remove:
            history.remove(entry)
