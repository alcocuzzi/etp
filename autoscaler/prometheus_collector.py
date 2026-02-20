"""
Prometheus Metrics Collector
============================
Queries the Prometheus HTTP API for CPU and memory usage of the webapp pods,
returns a pandas DataFrame ready to be serialised to CSV and passed to the AI.
"""

import io
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import requests

from config import PrometheusConfig

logger = logging.getLogger(__name__)


@dataclass
class MetricSnapshot:
    """A single moment-in-time reading for the whole deployment."""
    timestamp: datetime
    cpu_millicores: float             # avg CPU usage per pod (millicores)
    memory_mebibytes: float           # avg memory usage per pod (MiB)
    cpu_request_millicores: float     # avg CPU request per pod (millicores)
    memory_request_mebibytes: float   # avg memory request per pod (MiB)
    pod_count: int                    # number of Running pods


class PrometheusCollector:
    """Thin wrapper around the Prometheus HTTP query_range API."""

    def __init__(self, cfg: PrometheusConfig):
        self.cfg = cfg
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_history(self) -> pd.DataFrame:
        """
        Fetch the last `history_minutes` of metrics.
        Returns a DataFrame with columns:
            timestamp, cpu_millicores, memory_mebibytes,
            cpu_request_millicores, memory_request_mebibytes, pod_count
        """
        end = time.time()
        start = end - self.cfg.history_minutes * 60

        cpu_df      = self._query_range(self._cpu_query(), start, end)
        mem_df      = self._query_range(self._memory_query(), start, end)
        cpu_req_df  = self._query_range(self._cpu_request_query(), start, end)
        mem_req_df  = self._query_range(self._memory_request_query(), start, end)
        pod_df      = self._query_range(self._pod_count_query(), start, end)

        df = self._merge(cpu_df, mem_df, cpu_req_df, mem_req_df, pod_df)
        logger.debug("Fetched %d metric rows from Prometheus", len(df))
        return df

    def to_csv(self, df: pd.DataFrame) -> str:
        """Serialise the DataFrame to a compact CSV string."""
        buf = io.StringIO()
        df.to_csv(buf, index=False, float_format="%.4f")
        return buf.getvalue()

    def current_snapshot(self) -> MetricSnapshot:
        """Return a single snapshot of the current moment."""
        df = self.fetch_history()
        if df.empty:
            raise RuntimeError("No metrics returned from Prometheus")
        row = df.iloc[-1]
        return MetricSnapshot(
            timestamp=row["timestamp"],
            cpu_millicores=row["cpu_millicores"],
            memory_mebibytes=row["memory_mebibytes"],
            cpu_request_millicores=row["cpu_request_millicores"],
            memory_request_mebibytes=row["memory_request_mebibytes"],
            pod_count=int(row["pod_count"]),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _cpu_query(self) -> str:
        # avg CPU usage in millicores — no `container` label on cAdvisor metrics here
        ns, sel = self.cfg.namespace, self.cfg.pod_selector
        return (
            f'avg(rate(container_cpu_usage_seconds_total{{'
            f'namespace="{ns}",pod=~"{sel}"}}[1m])) * 1000'
        )

    def _memory_query(self) -> str:
        # avg memory usage in mebibytes — no `container` label on cAdvisor metrics here
        ns, sel = self.cfg.namespace, self.cfg.pod_selector
        return (
            f'avg(container_memory_usage_bytes{{'
            f'namespace="{ns}",pod=~"{sel}"}}) / (1024 * 1024)'
        )

    def _cpu_request_query(self) -> str:
        # avg CPU request in millicores — nginx container only (excludes exporter sidecar)
        ns, sel = self.cfg.namespace, self.cfg.pod_selector
        return (
            f'avg(kube_pod_container_resource_requests{{'
            f'resource="cpu",container="nginx",'
            f'namespace="{ns}",pod=~"{sel}"}}) * 1000'
        )

    def _memory_request_query(self) -> str:
        # avg memory request in mebibytes — nginx container only
        ns, sel = self.cfg.namespace, self.cfg.pod_selector
        return (
            f'avg(kube_pod_container_resource_requests{{'
            f'resource="memory",container="nginx",'
            f'namespace="{ns}",pod=~"{sel}"}}) / (1024 * 1024)'
        )

    def _pod_count_query(self) -> str:
        # kube_pod_status_phase is a 0/1 gauge per phase; sum the Running==1 values
        ns, sel = self.cfg.namespace, self.cfg.pod_selector
        return (
            f'sum(kube_pod_status_phase{{'
            f'phase="Running",namespace="{ns}",pod=~"{sel}"}}) or vector(0)'
        )


    def _query_range(self, query: str, start: float, end: float) -> pd.DataFrame:
        params = {
            "query": query,
            "start": start,
            "end": end,
            "step": self.cfg.step_seconds,
        }
        try:
            resp = self._session.get(
                f"{self.cfg.base_url}/api/v1/query_range",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Prometheus query failed: %s", exc)
            return pd.DataFrame()

        data = resp.json()
        if data.get("status") != "success":
            logger.error("Prometheus returned error: %s", data)
            return pd.DataFrame()

        results = data["data"]["result"]
        if not results:
            logger.warning("Empty result for query: %s", query[:80])
            return pd.DataFrame()

        # Aggregate all series (sum already applied in PromQL, but just in case)
        rows: list[dict] = []
        for series in results:
            for ts, val in series["values"]:
                rows.append({"timestamp": float(ts), "value": float(val)})

        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        return df.groupby("timestamp", as_index=False)["value"].sum()

    def _merge(
        self,
        cpu_df: pd.DataFrame,
        mem_df: pd.DataFrame,
        cpu_req_df: pd.DataFrame,
        mem_req_df: pd.DataFrame,
        pod_df: pd.DataFrame,
    ) -> pd.DataFrame:
        if cpu_df.empty or mem_df.empty:
            return pd.DataFrame()

        # Build from CPU usage as the base
        df = cpu_df.rename(columns={"value": "cpu_millicores"})
        df = df.merge(
            mem_df.rename(columns={"value": "memory_mebibytes"}),
            on="timestamp",
            how="inner",
        )

        # CPU requests (may be missing if kube-state-metrics not scraped)
        if not cpu_req_df.empty:
            df = df.merge(
                cpu_req_df.rename(columns={"value": "cpu_request_millicores"}),
                on="timestamp",
                how="left",
            )
        else:
            df["cpu_request_millicores"] = 100.0  # fallback: 100m from values.yaml

        # Memory requests
        if not mem_req_df.empty:
            df = df.merge(
                mem_req_df.rename(columns={"value": "memory_request_mebibytes"}),
                on="timestamp",
                how="left",
            )
        else:
            df["memory_request_mebibytes"] = 64.0  # fallback: 64Mi from values.yaml

        # Pod count
        if not pod_df.empty:
            df = df.merge(
                pod_df.rename(columns={"value": "pod_count"}),
                on="timestamp",
                how="left",
            )
        else:
            df["pod_count"] = 1

        df["cpu_request_millicores"] = df["cpu_request_millicores"].fillna(100.0)
        df["memory_request_mebibytes"] = df["memory_request_mebibytes"].fillna(64.0)
        df["pod_count"] = df["pod_count"].fillna(1).clip(lower=1).astype(int)
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
