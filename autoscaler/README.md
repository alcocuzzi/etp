# Prediction-Based Autoscaler

Thesis experiment: **Conventional HPA vs Ollama AI Prediction autoscaling** on a Kubernetes deployment.

## Architecture

```
Prometheus  ──►  prometheus_collector.py  ──►  pandas DataFrame  ──►  CSV
                                                      │
                                                      ▼
                                          ollama_predictor.py
                                    (openai-compatible call to Ollama)
                                          model: qwen2.5-coder:3b
                                                      │
                                    ┌─────────────────┴──────────────────┐
                                    ▼                                    ▼
                           hpa_simulator.py                     k8s_scaler.py
                       (what HPA would decide)            (applies AI decision)
                                    │                                    │
                                    └──────────── main.py ───────────────┘
                                                 (logs both to CSV)
```

## Quick Start

### 1. Install Ollama and pull the model

**macOS (Homebrew):**
```bash
brew install ollama
```

**Or download directly:** https://ollama.com/download

After installation, start the Ollama server and pull the model:

```bash
# Start Ollama (runs in background; on macOS it auto-starts after brew install)
ollama serve &

# Pull the model used by this experiment (~2 GB)
ollama pull qwen2.5-coder:3b

# Verify it is available
ollama ls
# Expected output includes: qwen2.5-coder:3b
```

> **Note:** Ollama must be running (`ollama serve`) whenever you run the autoscaler. On macOS with Homebrew it typically starts automatically as a background service.

### 2. Install Python dependencies

```bash
cd etp/autoscaler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Verify prerequisites

```bash
# Prometheus accessible?
curl http://localhost:9090/-/healthy

# Ollama running and model available?
ollama ls   # should show qwen2.5-coder:3b
curl http://localhost:11434/api/tags   # should return JSON with the model list

# kubectl context correct?
kubectl config current-context   # should show: docker-desktop
kubectl get deployment webapp

# metrics-server running (needed for HPA)?
kubectl top nodes
```

### 4. Run the experiment

#### Phase A – Conventional HPA observation

```bash
# Apply HPA (or let main.py create it automatically)
kubectl apply -f hpa.yaml

# Start observer – HPA governs, AI predictions are logged only
python3 main.py --mode hpa_only --duration 30 --interval 30

# Terminal 2: inject CPU stress mid-experiment
python3 cpu_stressor.py --workers 4 --duration 180

# Terminal 3: inject memory stress
python3 memory_stressor.py --mb 55 --duration 180

```

#### Phase B – AI Prediction autoscaling

```bash
# AI takes over (HPA is removed automatically)
python3 main.py --mode ai_scaler --duration 30 --interval 30

# Terminal 2: inject CPU stress mid-experiment
python3 cpu_stressor.py --workers 4 --duration 180

# Terminal 3: inject memory stress
python3 memory_stressor.py --mb 55 --duration 180
```

#### Phase C – Side-by-side comparison (observation only)

```bash
python3 main.py --mode comparison --duration 60 --interval 30
```

### 5. Analyse results

Both phases write to `./experiment_data/experiment_<mode>_<timestamp>.csv`.

Key columns for thesis comparison:

| Column | Description |
|--------|-------------|
| `timestamp` | UTC timestamp of the measurement |
| `mode` | Scaling mode (`hpa_only`, `ai_scaler`, `comparison`) |
| `current_replicas` | Actual replicas at the time |
| `cpu_millicores` | Average CPU usage per pod (millicores) |
| `memory_mebibytes` | Average memory usage per pod (MiB) |
| `hpa_sim_desired` | What the HPA algorithm would have chosen |
| `hpa_sim_cpu_ratio` | CPU utilisation ratio used by HPA sim |
| `hpa_sim_mem_ratio` | Memory utilisation ratio used by HPA sim |
| `hpa_current_replicas` | Replicas reported by the live HPA object |
| `hpa_desired_replicas` | What the live HPA chose |
| `ai_recommended_replicas` | What Ollama predicted |
| `ai_confidence` | Model confidence (0–1) |
| `ai_reasoning` | Full model reasoning text |
| `applied_replicas` | What was actually applied to the deployment |
| `applied_by` | Who applied the change (`ai`, `hpa`, or `none`) |

## CLI Reference

### main.py

```
python3 main.py [OPTIONS]

Options:
  --mode            hpa_only | ai_scaler | comparison  (default: comparison)
  --interval        Control loop interval in seconds   (default: 30)
  --duration        Experiment duration in minutes, 0=∞ (default: 0)
  --prometheus-url  Prometheus URL  (default: http://localhost:9090)
  --ollama-url      Ollama base URL (default: http://localhost:11434/v1)
  --model           Ollama model    (default: qwen2.5-coder:3b)
  --namespace       K8s namespace   (default: default)
  --deployment      Deployment name (default: webapp)
  --min-replicas    HPA/AI minimum  (default: 1)
  --max-replicas    HPA/AI maximum  (default: 10)
  --cpu-target      CPU utilisation target % — must match hpa.yaml (default: 60)
  --memory-target   Memory utilisation target % — must match hpa.yaml (default: 80)
  --history-minutes Minutes of Prometheus history to send AI (default: 15)
  --output-dir      CSV output dir  (default: ./experiment_data)
  --verbose         Enable DEBUG logging
```

### cpu_stressor.py

Injects CPU load into webapp pods via `kubectl exec`. Re-discovers pods every `--refresh` seconds so newly scaled pods are also stressed.

```
python3 cpu_stressor.py [OPTIONS]

Options:
  --workers N    Parallel busy-loop threads per pod  (default: 2)
  --duration S   How long to stress in seconds       (default: 300)
  --max-pods N   Cap the number of pods to stress    (default: all)
  --refresh S    Pod re-discovery interval in seconds (default: 15)
```

Example:
```bash
python3 cpu_stressor.py --workers 4 --duration 180
```

### memory_stressor.py

Allocates memory inside each pod via `/dev/shm` (tmpfs, counted by cgroups). The nginx container has a 64 MiB `/dev/shm` limit, so keep `--mb` at or below 60.

```
python3 memory_stressor.py [OPTIONS]

Options:
  --mb N        Megabytes to allocate per pod  (default: 50)
  --duration S  How long to hold the memory in seconds (default: 300)
  --max-pods N  Cap the number of pods        (default: all)
```

Example:
```bash
python3 memory_stressor.py --mb 55 --duration 180
```

### load_generator.py

Sends HTTP load to the webapp via an auto-managed `kubectl port-forward`. Use this **instead of `http://localhost:8080`** because Docker Desktop may intercept that port.

```
python3 load_generator.py [OPTIONS]

Options:
  --url URL           Target URL          (default: http://localhost:18080)
  --rps N             Constant requests/s (default: 50)
  --duration S        Total duration in seconds (default: 300)
  --ramp-start N      Starting RPS for linear ramp
  --ramp-end N        Ending RPS for linear ramp
  --ramp-duration S   Duration of ramp phase in seconds
  --hold S            Extra seconds to hold at peak RPS after ramp
```

Examples:
```bash
# Constant load
python3 load_generator.py --rps 100 --duration 300

# Ramp from 10 to 150 RPS over 2 minutes, then hold for 3 minutes
python3 load_generator.py --ramp-start 10 --ramp-end 150 --ramp-duration 120 --hold 180
```

## Files

| File | Purpose |
|------|---------|
| `config.py` | All configuration dataclasses (single source of truth for targets) |
| `prometheus_collector.py` | Prometheus HTTP API client |
| `ollama_predictor.py` | AI prediction via direct OpenAI-compatible call to Ollama |
| `k8s_scaler.py` | Kubernetes API — reads replicas, patches deployment, manages HPA |
| `hpa_simulator.py` | Pure-Python replication of the Kubernetes HPA algorithm |
| `main.py` | Control loop and CLI |
| `cpu_stressor.py` | Injects CPU load into webapp pods via `kubectl exec` |
| `memory_stressor.py` | Allocates memory inside pods via `/dev/shm` |
| `load_generator.py` | Async HTTP load generator with ramp support (auto port-forward) |
| `hpa.yaml` | Standalone HPA manifest (CPU 60%, memory 80%, scale-down 60s) |
| `requirements.txt` | Python dependencies |
