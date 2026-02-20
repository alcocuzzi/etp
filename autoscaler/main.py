"""
Main Orchestrator
=================
Runs the control loop that ties everything together:

  1. Collect Prometheus metrics  →  CSV
  2. Run HPA simulator           →  what conventional HPA would do
  3. Run Ollama AI predictor     →  what the AI recommends
  4. Apply scaling action        →  depends on --mode
  5. Append row to experiment CSV for thesis analysis

Modes
-----
  hpa_only    – HPA governs scaling; AI prediction is logged but not applied.
  ai_scaler   – AI prediction governs scaling; HPA is removed.
  comparison  – Neither changes anything; both recommendations are logged.

Usage
-----
    python main.py --mode comparison --interval 30 --duration 60
    python main.py --mode ai_scaler --interval 30
    python main.py --mode hpa_only  --interval 30 --duration 60
"""

import argparse
import asyncio
import csv
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from config import AppConfig, ExperimentConfig, ScalingMode
from hpa_simulator import HPASimulator
from k8s_scaler import KubernetesScaler
from ollama_predictor import OllamaPredictor
from prometheus_collector import PrometheusCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# CSV output helpers
# ---------------------------------------------------------------------------

FIELDNAMES = [
    "timestamp",
    "mode",
    "current_replicas",
    "ready_replicas",
    # Raw Prometheus metrics (exact query units)
    "cpu_millicores",                # avg(rate(container_cpu_usage_seconds_total[1m])) * 1000
    "memory_mebibytes",              # avg(container_memory_usage_bytes) / (1024*1024)
    "cpu_request_millicores",        # avg(kube_pod_container_resource_requests{resource="cpu"}) * 1000
    "memory_request_mebibytes",      # avg(kube_pod_container_resource_requests{resource="memory"}) / (1024*1024)
    "pod_count",                     # count(kube_pod_status_phase{phase="Running"})
    # HPA simulator
    "hpa_sim_desired",
    "hpa_sim_raw_desired",
    "hpa_sim_cpu_desired",
    "hpa_sim_mem_desired",
    "hpa_sim_action",
    "hpa_sim_cpu_ratio",
    "hpa_sim_mem_ratio",
    # Live HPA (if it exists)
    "hpa_current_replicas",
    "hpa_desired_replicas",
    # AI prediction
    "ai_recommended_replicas",
    "ai_predicted_cpu_millicores",
    "ai_predicted_memory_mebibytes",
    "ai_confidence",
    "ai_reasoning",
    # Applied action
    "applied_replicas",
    "applied_by",
]


def get_csv_path(output_dir: str, mode: ScalingMode) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return out / f"experiment_{mode.value}_{ts}.csv"


# ---------------------------------------------------------------------------
# Control loop
# ---------------------------------------------------------------------------

async def run_loop(cfg: AppConfig) -> None:
    mode = cfg.experiment.mode
    interval = cfg.experiment.interval_seconds
    duration_s = cfg.experiment.duration_minutes * 60  # 0 = infinite

    collector = PrometheusCollector(cfg.prometheus)
    scaler = KubernetesScaler(cfg.kubernetes)
    hpa_sim = HPASimulator(cfg.kubernetes)  # picks up cpu/memory targets from config
    predictor = OllamaPredictor(cfg.ollama, cfg.kubernetes)

    csv_path = get_csv_path(cfg.experiment.output_dir, mode)
    logger.info("Writing experiment data to %s", csv_path)

    # --- Mode setup ---------------------------------------------------
    if mode == ScalingMode.AI_SCALER:
        logger.info("AI_SCALER mode: removing HPA so AI controls replica count")
        scaler.ensure_hpa_disabled()
    else:
        logger.info("Ensuring HPA exists for mode=%s", mode.value)
        scaler.ensure_hpa_exists(
            cpu_target_percent=int(cfg.kubernetes.cpu_target_utilization * 100),
            memory_target_percent=int(cfg.kubernetes.memory_target_utilization * 100),
            scale_down_stabilization_s=60,
            min_replicas=cfg.kubernetes.min_replicas,
            max_replicas=cfg.kubernetes.max_replicas,
        )

    start_time = time.monotonic()
    iteration = 0

    with open(csv_path, "w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writeheader()
        csv_file.flush()

        while True:
            iteration += 1
            now_ts = datetime.now(timezone.utc).isoformat()
            iter_start = time.monotonic()

            logger.info("=" * 60)
            logger.info("Iteration %d  [%s]  mode=%s", iteration, now_ts, mode.value)

            row: dict = {f: "" for f in FIELDNAMES}
            row["timestamp"] = now_ts
            row["mode"] = mode.value

            # 1. Current k8s state ----------------------------------------
            current_replicas = scaler.get_current_replicas()
            ready_replicas = scaler.get_ready_replicas()
            row["current_replicas"] = current_replicas
            row["ready_replicas"] = ready_replicas
            logger.info("Current replicas: desired=%d  ready=%d",
                        current_replicas, ready_replicas)

            # 2. Prometheus metrics ----------------------------------------
            try:
                df = collector.fetch_history()
                if df.empty:
                    logger.warning("No metrics from Prometheus – skipping iteration")
                    _sleep_until_next(interval, iter_start)
                    continue

                latest = df.iloc[-1]
                row["cpu_millicores"]           = round(latest["cpu_millicores"], 3)
                row["memory_mebibytes"]          = round(latest["memory_mebibytes"], 3)
                row["cpu_request_millicores"]    = round(latest["cpu_request_millicores"], 3)
                row["memory_request_mebibytes"]  = round(latest["memory_request_mebibytes"], 3)
                row["pod_count"]                 = int(latest["pod_count"])

                logger.info(
                    "Metrics: cpu=%.1f m  mem=%.1f MiB  cpu_req=%.1f m  mem_req=%.1f MiB  pods=%d",
                    latest["cpu_millicores"],
                    latest["memory_mebibytes"],
                    latest["cpu_request_millicores"],
                    latest["memory_request_mebibytes"],
                    latest["pod_count"],
                )
            except Exception as exc:
                logger.error("Prometheus fetch failed: %s", exc)
                _sleep_until_next(interval, iter_start)
                continue

            # 3. HPA Simulator -------------------------------------------
            hpa_result = hpa_sim.compute(
                current_replicas=current_replicas,
                cpu_millicores=latest["cpu_millicores"],
                memory_mebibytes=latest["memory_mebibytes"],
                cpu_request_millicores=latest["cpu_request_millicores"],
                memory_request_mebibytes=latest["memory_request_mebibytes"],
                timestamp_s=time.time(),
            )
            row.update(hpa_result)
            logger.info(
                "HPA simulator → %d replicas (action=%s, cpu_ratio=%.2f)",
                hpa_result["hpa_sim_desired"],
                hpa_result["hpa_sim_action"],
                hpa_result["hpa_sim_cpu_ratio"],
            )

            # 4. Live HPA status (if exists) --------------------------------
            hpa_status = scaler.get_hpa_status()
            if hpa_status:
                row["hpa_current_replicas"] = hpa_status.get("hpa_current_replicas", "")
                row["hpa_desired_replicas"] = hpa_status.get("hpa_desired_replicas", "")

            # 5. Ollama AI prediction ----------------------------------------
            try:
                prediction = await predictor.predict_from_df(df, current_replicas)
                row["ai_recommended_replicas"]     = prediction.recommended_replicas
                row["ai_predicted_cpu_millicores"]   = round(prediction.predicted_cpu_millicores, 3)
                row["ai_predicted_memory_mebibytes"] = round(prediction.predicted_memory_mebibytes, 3)
                row["ai_confidence"] = round(prediction.confidence, 3)
                row["ai_reasoning"] = prediction.reasoning.replace("\n", " ")
                logger.info(
                    "AI prediction → %d replicas (conf=%.2f)",
                    prediction.recommended_replicas,
                    prediction.confidence,
                )
            except Exception as exc:
                logger.error("Ollama prediction failed: %s", exc)
                prediction = None

            # 6. Apply scaling decision -----------------------------------
            applied_replicas = current_replicas
            applied_by = "none"

            if mode == ScalingMode.AI_SCALER and prediction is not None:
                if prediction.recommended_replicas != current_replicas:
                    ok = scaler.scale_deployment(prediction.recommended_replicas)
                    if ok:
                        applied_replicas = prediction.recommended_replicas
                        applied_by = "ai"
                else:
                    applied_by = "ai_no_change"
                    logger.info("AI: no change needed (already at %d replicas)", current_replicas)

            elif mode == ScalingMode.HPA_ONLY:
                # HPA governs; we just observe
                applied_by = "hpa"
                applied_replicas = hpa_status.get("hpa_desired_replicas", current_replicas)

            else:  # COMPARISON – observe only
                applied_by = "observation_only"

            row["applied_replicas"] = applied_replicas
            row["applied_by"] = applied_by

            # 7. Write CSV row -------------------------------------------
            writer.writerow(row)
            csv_file.flush()

            # 8. Check duration ------------------------------------------
            elapsed = time.monotonic() - start_time
            if duration_s > 0 and elapsed >= duration_s:
                logger.info("Experiment duration reached (%.0f s). Exiting.", elapsed)
                break

            _sleep_until_next(interval, iter_start)

    logger.info("Experiment complete. Data saved to %s", csv_path)


def _sleep_until_next(interval: int, iter_start: float) -> None:
    elapsed = time.monotonic() - iter_start
    sleep_for = max(0.0, interval - elapsed)
    if sleep_for > 0:
        logger.debug("Sleeping %.1f s until next iteration", sleep_for)
        time.sleep(sleep_for)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> AppConfig:
    parser = argparse.ArgumentParser(
        description="Prediction-based vs HPA autoscaler experiment"
    )
    parser.add_argument(
        "--mode",
        choices=[m.value for m in ScalingMode],
        default=ScalingMode.COMPARISON.value,
        help="Scaling mode (default: comparison)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Control loop interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Experiment duration in minutes, 0=infinite (default: 0)",
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://localhost:9090",
        help="Prometheus base URL",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://localhost:11434/v1",
        help="Ollama OpenAI-compatible base URL",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5-coder:3b",
        help="Ollama model name",
    )
    parser.add_argument(
        "--namespace",
        default="default",
        help="Kubernetes namespace",
    )
    parser.add_argument(
        "--deployment",
        default="webapp",
        help="Deployment name",
    )
    parser.add_argument(
        "--min-replicas",
        type=int,
        default=1,
    )
    parser.add_argument(
        "--max-replicas",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--cpu-target",
        type=int,
        default=60,
        help="CPU utilisation target %% (must match hpa.yaml averageUtilization, default: 60)",
    )
    parser.add_argument(
        "--memory-target",
        type=int,
        default=80,
        help="Memory utilisation target %% (must match hpa.yaml averageUtilization, default: 80)",
    )
    parser.add_argument(
        "--output-dir",
        default="./experiment_data",
        help="Directory for CSV output",
    )
    parser.add_argument(
        "--history-minutes",
        type=int,
        default=15,
        help="Minutes of history to send to Ollama",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = AppConfig()
    cfg.experiment.mode = ScalingMode(args.mode)
    cfg.experiment.interval_seconds = args.interval
    cfg.experiment.duration_minutes = args.duration
    cfg.experiment.output_dir = args.output_dir
    cfg.experiment.verbose = args.verbose

    cfg.prometheus.base_url = args.prometheus_url
    cfg.prometheus.namespace = args.namespace
    cfg.prometheus.history_minutes = args.history_minutes

    cfg.ollama.base_url = args.ollama_url
    cfg.ollama.model = args.model

    cfg.kubernetes.namespace = args.namespace
    cfg.kubernetes.deployment = args.deployment
    cfg.kubernetes.min_replicas = args.min_replicas
    cfg.kubernetes.max_replicas = args.max_replicas
    cfg.kubernetes.cpu_target_utilization = args.cpu_target / 100.0
    cfg.kubernetes.memory_target_utilization = args.memory_target / 100.0

    return cfg


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = parse_args()
    asyncio.run(run_loop(cfg))
