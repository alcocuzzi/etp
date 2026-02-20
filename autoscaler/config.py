"""
Autoscaler Configuration
========================
Central config for the Prediction-Based vs HPA autoscaler experiment.
"""

from dataclasses import dataclass, field
from enum import Enum


class ScalingMode(str, Enum):
    """Which autoscaling strategy is active."""
    HPA_ONLY = "hpa_only"          # Apply and observe conventional HPA
    AI_SCALER = "ai_scaler"        # Let the AI prediction drive replica count
    COMPARISON = "comparison"      # Observe HPA, compute AI prediction, log both (no changes applied)


@dataclass
class PrometheusConfig:
    base_url: str = "http://localhost:9090"
    # Label selectors for the webapp pods
    namespace: str = "default"
    deployment: str = "webapp"
    pod_selector: str = "webapp-.*"
    # How many minutes of look-back history to feed the AI
    history_minutes: int = 15
    # Step resolution (Prometheus range query step)
    step_seconds: int = 30


@dataclass
class OllamaConfig:
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "qwen2.5-coder:3b"
    # Prediction horizon
    predict_minutes_ahead: int = 5
    # Timeout for Ollama request (seconds)
    timeout: int = 120


@dataclass
class KubernetesConfig:
    # If None, uses the current kubectl context (works for docker-desktop AND EKS)
    kubeconfig_path: str | None = None
    namespace: str = "default"
    deployment: str = "webapp"
    hpa_name: str = "webapp-hpa"
    min_replicas: int = 1
    max_replicas: int = 10
    # Must match hpa.yaml averageUtilization values (as fractions)
    cpu_target_utilization: float = 0.60   # 60% → averageUtilization: 60
    memory_target_utilization: float = 0.80  # 80% → averageUtilization: 80


@dataclass
class ExperimentConfig:
    # How often the control loop runs (seconds)
    interval_seconds: int = 30
    # How long to run the experiment total (0 = run forever)
    duration_minutes: int = 0
    # Which mode to run
    mode: ScalingMode = ScalingMode.COMPARISON
    # CSV output directory for analysis
    output_dir: str = "./experiment_data"
    # Whether to print verbose logs
    verbose: bool = True


@dataclass
class AppConfig:
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    kubernetes: KubernetesConfig = field(default_factory=KubernetesConfig)
    experiment: ExperimentConfig = field(default_factory=ExperimentConfig)


# ---------------------------------------------------------------------------
# Singleton – import and mutate before first use if you need custom settings
# ---------------------------------------------------------------------------
CONFIG = AppConfig()
