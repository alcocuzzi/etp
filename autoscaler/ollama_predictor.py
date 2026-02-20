"""
Ollama AI Predictor
===================
Sends the last N minutes of CPU/memory CSV data to a local Ollama model and
returns a structured prediction of how many pods are needed in the next
``predict_minutes_ahead`` minutes.

Strategy: call the Ollama OpenAI-compatible endpoint directly (bypassing
pydantic-ai tool-calling, which small models handle poorly), request plain
JSON in the prompt, strip any markdown fences from the response, then
validate the JSON with Pydantic ourselves.

Model: qwen2.5-coder:3b
"""

import json
import logging
import math
import re
import textwrap
from typing import Annotated

import openai
import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from config import KubernetesConfig, OllamaConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schema – what the AI must return
# ---------------------------------------------------------------------------

class ScalingPrediction(BaseModel):
    """Structured prediction returned by the AI."""

    recommended_replicas: Annotated[
        int,
        Field(ge=1, le=20, description="Number of pods recommended for the next window"),
    ]
    predicted_cpu_millicores: Annotated[
        float,
        Field(ge=0.0, description="Expected average CPU usage per pod in millicores"),
    ]
    predicted_memory_mebibytes: Annotated[
        float,
        Field(ge=0.0, description="Expected average memory usage per pod in MiB"),
    ]
    confidence: Annotated[
        float,
        Field(ge=0.0, le=1.0, description="Confidence score between 0 and 1"),
    ]
    reasoning: str = Field(description="Short explanation of the prediction")


# ---------------------------------------------------------------------------
# Predictor
# ---------------------------------------------------------------------------

class OllamaPredictor:
    """
    Calls Ollama via the OpenAI-compatible REST API directly.
    Structured output is enforced by prompt + Pydantic validation,
    NOT by tool-calling (which tiny models don't support reliably).
    """

    def __init__(self, ollama_cfg: OllamaConfig, k8s_cfg: KubernetesConfig):
        self.cfg = ollama_cfg
        self.k8s_cfg = k8s_cfg
        self._client = openai.AsyncOpenAI(
            base_url=ollama_cfg.base_url,
            api_key=ollama_cfg.api_key,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def predict(
        self,
        csv_data: str,
        current_replicas: int,
        baseline_replicas: int | None = None,
        avg_cpu: float = 0.0,
        avg_mem: float = 0.0,
        cpu_ratio: float = 1.0,
        mem_ratio: float = 1.0,
    ) -> ScalingPrediction:
        """
        Ask the AI to predict the ideal replica count for the next window.
        Retries up to 3 times if the model returns malformed JSON.
        """
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user",   "content": self._build_prompt(
                csv_data, current_replicas,
                baseline_replicas=baseline_replicas,
                avg_cpu=avg_cpu, avg_mem=avg_mem,
                cpu_ratio=cpu_ratio, mem_ratio=mem_ratio,
            )},
        ]
        logger.debug("Sending prompt to Ollama model=%s", self.cfg.model)

        last_exc: Exception | None = None
        for attempt in range(1, 4):  # up to 3 attempts
            try:
                response = await self._client.chat.completions.create(
                    model=self.cfg.model,
                    messages=messages,
                    temperature=0,
                    max_tokens=200,   # JSON fits in <100 tokens; cap to prevent rambling
                    timeout=self.cfg.timeout,
                )
                raw = response.choices[0].message.content or ""
                prediction = self._parse(raw)
                prediction.recommended_replicas = max(
                    self.k8s_cfg.min_replicas,
                    min(self.k8s_cfg.max_replicas, prediction.recommended_replicas),
                )
                logger.info(
                    "AI prediction → %d replicas (conf=%.2f, attempt=%d): %s",
                    prediction.recommended_replicas,
                    prediction.confidence,
                    attempt,
                    prediction.reasoning,
                )
                return prediction
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("Attempt %d/%d – bad JSON from model: %s", attempt, 3, exc)
                last_exc = exc
                # Feed the error back so the model can self-correct
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous response was not valid JSON. Error: {exc}\n"
                        "Reply ONLY with the corrected JSON object, nothing else."
                    ),
                })

        raise RuntimeError(f"Ollama failed to return valid JSON after 3 attempts: {last_exc}")

    async def predict_from_df(
        self, df: pd.DataFrame, current_replicas: int
    ) -> ScalingPrediction:
        """Convenience wrapper that accepts a DataFrame directly."""
        tail = df.tail(10)
        csv_data = tail.to_csv(index=False, float_format="%.2f")

        # --- Compute the HPA formula in Python (reliable arithmetic) --------
        recent = tail.tail(3)   # last 3 rows = most recent ~90 seconds
        avg_cpu = float(recent["cpu_millicores"].mean())
        avg_mem = float(recent["memory_mebibytes"].mean())
        cpu_req = float(recent["cpu_request_millicores"].iloc[-1])
        mem_req = float(recent["memory_request_mebibytes"].iloc[-1])

        cpu_ratio = avg_cpu / (cpu_req * self.k8s_cfg.cpu_target_utilization) if cpu_req > 0 else 1.0
        mem_ratio = avg_mem / (mem_req * self.k8s_cfg.memory_target_utilization) if mem_req > 0 else 1.0
        raw = math.ceil(current_replicas * max(cpu_ratio, mem_ratio))
        baseline = max(self.k8s_cfg.min_replicas,
                       min(self.k8s_cfg.max_replicas, raw))

        logger.info(
            "HPA formula (Python) → cpu_ratio=%.3f mem_ratio=%.3f raw=%d baseline=%d",
            cpu_ratio, mem_ratio, raw, baseline,
        )

        return await self.predict(
            csv_data, current_replicas,
            baseline_replicas=baseline,
            avg_cpu=avg_cpu, avg_mem=avg_mem,
            cpu_ratio=cpu_ratio, mem_ratio=mem_ratio,
        )

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(raw: str) -> ScalingPrediction:
        """Strip markdown fences then parse + validate with Pydantic."""
        # Remove ```json ... ``` or ``` ... ``` wrappers
        text = re.sub(r"^```[a-zA-Z]*\n?", "", raw.strip())
        text = re.sub(r"\n?```$", "", text.strip())
        # Extract first {...} block in case model adds preamble
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object found in response: {raw[:200]}")
        return ScalingPrediction.model_validate(json.loads(match.group()))

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        return textwrap.dedent(f"""
            You are a Kubernetes autoscaling assistant.
            The controller has already computed the baseline replica count using
            the HPA formula. Your job is to review the recent metric TREND and
            decide whether to keep, increase, or decrease the baseline by 1-2
            pods based on whether load is rising or falling.

            CSV columns (per-pod averages, 30-second intervals):
              timestamp, cpu_millicores, memory_mebibytes,
              cpu_request_millicores, memory_request_mebibytes, pod_count

            Limits: min_replicas={self.k8s_cfg.min_replicas},
                    max_replicas={self.k8s_cfg.max_replicas}
            Targets: cpu={int(self.k8s_cfg.cpu_target_utilization*100)}% of cpu_request,
                     memory={int(self.k8s_cfg.memory_target_utilization*100)}% of mem_request

            Reply with ONLY a JSON object — no markdown, no explanation outside JSON:
            {{
              "recommended_replicas": <int>,
              "predicted_cpu_millicores": <float>,
              "predicted_memory_mebibytes": <float>,
              "confidence": <float 0-1>,
              "reasoning": "<one sentence: state trend direction and why you adjusted or kept baseline>"
            }}
        """).strip()

    def _build_prompt(
        self,
        csv_data: str,
        current_replicas: int,
        baseline_replicas: int | None = None,
        avg_cpu: float = 0.0,
        avg_mem: float = 0.0,
        cpu_ratio: float = 1.0,
        mem_ratio: float = 1.0,
    ) -> str:
        lines = csv_data.strip().splitlines()
        header = lines[0] if lines else ""
        recent = "\n".join(lines[-3:]) if len(lines) > 1 else ""
        baseline_str = str(baseline_replicas) if baseline_replicas is not None else "?"
        return textwrap.dedent(f"""
            Current running pods : {current_replicas}
            Formula baseline     : {baseline_str} replicas
              (avg_cpu={avg_cpu:.2f}m  cpu_ratio={cpu_ratio:.3f}
               avg_mem={avg_mem:.2f}Mi mem_ratio={mem_ratio:.3f})

            Most recent 3 rows:
            {header}
            {recent}

            Full history (for trend analysis only):
            {csv_data}

            Should the baseline of {baseline_str} be kept, increased, or decreased
            based on the metric trend? Return the JSON object now.
        """).strip()
