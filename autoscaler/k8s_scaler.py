"""
Kubernetes Scaler
=================
Reads the current replica count and applies scaling decisions to the
Deployment (or HPA) via the official kubernetes Python client.

Works with any context: docker-desktop, EKS, GKE, etc.
"""

import logging

from kubernetes import client, config as k8s_config
from kubernetes.client.rest import ApiException

from config import KubernetesConfig

logger = logging.getLogger(__name__)


class KubernetesScaler:
    """Thin wrapper around the k8s AppsV1 and AutoscalingV2 APIs."""

    def __init__(self, cfg: KubernetesConfig):
        self.cfg = cfg
        self._load_kube_config()
        self._apps_v1 = client.AppsV1Api()
        self._autoscaling_v2 = client.AutoscalingV2Api()
        self._autoscaling_v1 = client.AutoscalingV1Api()

    # ------------------------------------------------------------------
    # Replica introspection
    # ------------------------------------------------------------------

    def get_current_replicas(self) -> int:
        """Returns the current *desired* replica count of the Deployment."""
        try:
            dep = self._apps_v1.read_namespaced_deployment(
                name=self.cfg.deployment,
                namespace=self.cfg.namespace,
            )
            return dep.spec.replicas or 1
        except ApiException as exc:
            logger.error("Could not read Deployment replicas: %s", exc)
            return 1

    def get_ready_replicas(self) -> int:
        """Returns the number of actually *ready* pods."""
        try:
            dep = self._apps_v1.read_namespaced_deployment(
                name=self.cfg.deployment,
                namespace=self.cfg.namespace,
            )
            return dep.status.ready_replicas or 0
        except ApiException as exc:
            logger.error("Could not read ready replicas: %s", exc)
            return 0

    def get_hpa_status(self) -> dict:
        """
        Returns a dict with the current HPA state (desired / min / max /
        current metrics) or an empty dict if no HPA exists.
        """
        try:
            hpa = self._autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(
                name=self.cfg.hpa_name,
                namespace=self.cfg.namespace,
            )
            status = hpa.status
            return {
                "hpa_current_replicas": status.current_replicas,
                "hpa_desired_replicas": status.desired_replicas,
                "hpa_min_replicas": hpa.spec.min_replicas,
                "hpa_max_replicas": hpa.spec.max_replicas,
                "hpa_conditions": [
                    {"type": c.type, "status": c.status, "reason": c.reason}
                    for c in (status.conditions or [])
                ],
            }
        except ApiException as exc:
            if exc.status == 404:
                logger.debug("HPA '%s' not found in namespace '%s'",
                             self.cfg.hpa_name, self.cfg.namespace)
            else:
                logger.warning("Could not read HPA status: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Scaling actions
    # ------------------------------------------------------------------

    def scale_deployment(self, replicas: int) -> bool:
        """
        Directly patch the Deployment replicas.
        Returns True on success.
        This is used by the AI scaler mode (bypasses HPA).
        """
        replicas = max(self.cfg.min_replicas, min(self.cfg.max_replicas, replicas))
        patch = {"spec": {"replicas": replicas}}
        try:
            self._apps_v1.patch_namespaced_deployment(
                name=self.cfg.deployment,
                namespace=self.cfg.namespace,
                body=patch,
            )
            logger.info(
                "Scaled Deployment '%s' to %d replicas",
                self.cfg.deployment,
                replicas,
            )
            return True
        except ApiException as exc:
            logger.error("Failed to scale Deployment: %s", exc)
            return False

    def ensure_hpa_disabled(self) -> None:
        """
        In AI_SCALER mode we delete the HPA so it doesn't fight our patches.
        Safe to call if HPA doesn't exist.
        """
        try:
            self._autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler(
                name=self.cfg.hpa_name,
                namespace=self.cfg.namespace,
            )
            logger.info("Deleted HPA '%s' for AI_SCALER mode", self.cfg.hpa_name)
        except ApiException as exc:
            if exc.status != 404:
                logger.warning("Could not delete HPA: %s", exc)

    def ensure_hpa_exists(
        self,
        min_replicas: int | None = None,
        max_replicas: int | None = None,
        cpu_target_percent: int = 60,
        memory_target_percent: int = 80,
        scale_down_stabilization_s: int = 60,
    ) -> None:
        """
        Creates (or recreates when unhealthy) the HPA with CPU + memory metrics.
        Required for HPA_ONLY and COMPARISON modes.
        """
        min_r = min_replicas or self.cfg.min_replicas
        max_r = max_replicas or self.cfg.max_replicas

        # Check existence and health (ScalingActive condition)
        try:
            existing = self._autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(
                name=self.cfg.hpa_name,
                namespace=self.cfg.namespace,
            )
            conditions = existing.status.conditions or []
            scaling_active = next(
                (c for c in conditions if c.type == "ScalingActive"), None
            )
            if scaling_active and scaling_active.status == "True":
                logger.debug("HPA '%s' already exists and is healthy", self.cfg.hpa_name)
                return
            reason = scaling_active.reason if scaling_active else "unknown"
            logger.warning(
                "HPA '%s' ScalingActive=%s (%s) – recreating",
                self.cfg.hpa_name,
                scaling_active.status if scaling_active else "?",
                reason,
            )
            self._autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler(
                name=self.cfg.hpa_name, namespace=self.cfg.namespace
            )
        except ApiException as exc:
            if exc.status != 404:
                logger.error("Unexpected error checking HPA: %s", exc)
                return

        # Build HPA with CPU + memory metrics and tuned behavior
        hpa_body = client.V2HorizontalPodAutoscaler(
            metadata=client.V1ObjectMeta(
                name=self.cfg.hpa_name,
                namespace=self.cfg.namespace,
            ),
            spec=client.V2HorizontalPodAutoscalerSpec(
                scale_target_ref=client.V2CrossVersionObjectReference(
                    api_version="apps/v1",
                    kind="Deployment",
                    name=self.cfg.deployment,
                ),
                min_replicas=min_r,
                max_replicas=max_r,
                metrics=[
                    client.V2MetricSpec(
                        type="Resource",
                        resource=client.V2ResourceMetricSource(
                            name="cpu",
                            target=client.V2MetricTarget(
                                type="Utilization",
                                average_utilization=cpu_target_percent,
                            ),
                        ),
                    ),
                    client.V2MetricSpec(
                        type="Resource",
                        resource=client.V2ResourceMetricSource(
                            name="memory",
                            target=client.V2MetricTarget(
                                type="Utilization",
                                average_utilization=memory_target_percent,
                            ),
                        ),
                    ),
                ],
                behavior=client.V2HorizontalPodAutoscalerBehavior(
                    scale_up=client.V2HPAScalingRules(
                        stabilization_window_seconds=0,
                        policies=[
                            client.V2HPAScalingPolicy(
                                type="Percent", value=100, period_seconds=15
                            )
                        ],
                    ),
                    scale_down=client.V2HPAScalingRules(
                        stabilization_window_seconds=scale_down_stabilization_s,
                        policies=[
                            client.V2HPAScalingPolicy(
                                type="Pods", value=1, period_seconds=30
                            )
                        ],
                    ),
                ),
            ),
        )
        try:
            self._autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(
                namespace=self.cfg.namespace,
                body=hpa_body,
            )
            logger.info(
                "Created HPA '%s' (min=%d, max=%d, cpu=%d%%, mem=%d%%, scale_down_window=%ds)",
                self.cfg.hpa_name, min_r, max_r,
                cpu_target_percent, memory_target_percent, scale_down_stabilization_s,
            )
        except ApiException as exc:
            logger.error("Failed to create HPA: %s", exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_kube_config(self) -> None:
        if self.cfg.kubeconfig_path:
            k8s_config.load_kube_config(config_file=self.cfg.kubeconfig_path)
        else:
            try:
                k8s_config.load_incluster_config()
                logger.debug("Loaded in-cluster kubeconfig")
            except k8s_config.config_exception.ConfigException:
                k8s_config.load_kube_config()
                logger.debug("Loaded local kubeconfig")
