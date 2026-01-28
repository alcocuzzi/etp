# Kubernetes Lab with Docker Desktop

This repository contains Kubernetes resources for deploying a web application with monitoring and chaos engineering experiments using Docker Desktop's built-in Kubernetes cluster.

## Quick Start

For a complete fresh installation:

```bash
# 1. Enable Kubernetes in Docker Desktop (Settings → Kubernetes → Enable)

# 2. Clone this repository and navigate to the directory
cd etp

# 3. Create monitoring namespace and install monitoring stack
kubectl create namespace monitoring
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install monitoring prometheus-community/kube-prometheus-stack -f monitoring-values.yaml -n monitoring

# 4. Wait for monitoring pods to be ready (takes 2-3 minutes)
kubectl get pods -n monitoring -w

# 5. Install webapp with monitoring and dashboard
helm install webapp ./webapp-chart --set serviceMonitor.enabled=true --set grafanaDashboard.enabled=true

# 6. Install Chaos Mesh (optional)
helm repo add chaos-mesh https://charts.chaos-mesh.org
kubectl create namespace chaos-mesh
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
  --set dashboard.create=true
kubectl patch svc chaos-dashboard -n chaos-mesh -p '{"spec": {"type": "LoadBalancer"}}'

# 7. Configure RBAC for Chaos Dashboard
kubectl apply -f chaos-mesh-rbac.yaml

# 8. Get the access token for Chaos Dashboard
kubectl describe secret chaos-mesh-viewer-token -n default | grep "token:" | awk '{print $2}'
# Copy this token - you'll need it to access the Chaos Dashboard
```

**Access Points:**
- Grafana: http://localhost (admin/admin123)
- Prometheus: http://localhost:9090
- Webapp: http://localhost:8080
- Chaos Dashboard: http://localhost:2333

> 📘 **For complete cleanup and recreation instructions**, see [SETUP-GUIDE.md](SETUP-GUIDE.md)

## Contents

- **webapp-chart/**: Helm chart for an NGINX web application with Prometheus metrics
- **monitoring-values.yaml**: Configuration for Prometheus and Grafana monitoring stack
- **chaos-experiments/**: Chaos engineering experiments for testing resilience
- **chaos-mesh-rbac.yaml**: RBAC configuration for Chaos Mesh dashboard access

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed
- [kubectl](https://kubernetes.io/docs/tasks/tools/) installed (comes with Docker Desktop)
- [Helm](https://helm.sh/docs/intro/install/) installed (v3.x)
- Docker Desktop with Kubernetes enabled

## Getting Started

### 1. Enable Kubernetes in Docker Desktop

1. Open Docker Desktop
2. Go to **Settings** (gear icon) → **Kubernetes**
3. Check **Enable Kubernetes**
4. Click **Apply & Restart**
5. Wait for Kubernetes to start (the status indicator will turn green)

### 2. Configure Docker Desktop Resources (Optional)

For better performance, allocate sufficient resources:

1. Go to **Settings** → **Resources**
2. Set **CPUs**: 4 or more
3. Set **Memory**: 8 GB or more
4. Click **Apply & Restart**

### 3. Verify Kubernetes is Running

```bash
# Check kubectl is configured for docker-desktop
kubectl config current-context
# Should output: docker-desktop

# Verify cluster is running
kubectl cluster-info
kubectl get nodes
```

> **Note**: Docker Desktop automatically configures kubectl to use its Kubernetes cluster. LoadBalancer services are accessible via `localhost`.

## Deploying Monitoring Stack (Prometheus & Grafana)

Deploy the monitoring stack first to enable observability for your applications.

### 1. Add Prometheus Community Helm Repository

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
```

### 2. Create Monitoring Namespace

```bash
kubectl create namespace monitoring
```

### 3. Install kube-prometheus-stack

```bash
helm install monitoring prometheus-community/kube-prometheus-stack \
  -f monitoring-values.yaml \
  -n monitoring
```

### 4. Wait for All Pods to be Ready

```bash
kubectl get pods -n monitoring -w
```

Press `Ctrl+C` when all pods show `Running` status.

### 5. Verify Monitoring Services

```bash
kubectl get svc -n monitoring
```

You should see Grafana and Prometheus services as LoadBalancer type with `localhost` as EXTERNAL-IP.

## Accessing Monitoring Tools

### Access Grafana

With Docker Desktop, Grafana is exposed as a LoadBalancer service and accessible directly via `localhost`:

```
http://localhost
```

**Default Credentials:**
- Username: `admin`
- Password: `admin123`

> **Note**: The service runs on port 80. You can also access it via the NodePort shown in `kubectl get svc -n monitoring`.

### Access Prometheus

With Docker Desktop, Prometheus is exposed as a LoadBalancer service and accessible directly via `localhost`:

```
http://localhost:9090
```

### Access AlertManager (Port Forwarding Required)

AlertManager is a ClusterIP service, so it requires port forwarding:

```bash
kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-alertmanager 9093:9093
```

Open your browser to: `http://localhost:9093`

## Deploying the Web Application

Now that monitoring is set up, deploy the web application with monitoring enabled.

### 1. Install the Webapp with Monitoring

```bash
# Navigate to the repository directory
cd etp

# Install the webapp chart with ServiceMonitor and Grafana Dashboard enabled
helm install webapp ./webapp-chart \
  --set serviceMonitor.enabled=true \
  --set grafanaDashboard.enabled=true

# Verify the deployment
kubectl get deployments
kubectl get pods
kubectl get services
```

### 2. Check the Service Status

```bash
kubectl get svc
```

You should see output similar to:

```
NAME              TYPE           CLUSTER-IP      EXTERNAL-IP   PORT(S)          AGE
webapp-service    LoadBalancer   10.96.184.178   localhost     8080:30791/TCP   40s
```

> **Note**: Docker Desktop automatically maps LoadBalancer services to `localhost`.

### 3. Access the Application

With Docker Desktop, the webapp is accessible directly via `localhost`:

```
http://localhost:8080
```

Open your browser and navigate to the URL above. You'll see the NGINX welcome page.

The metrics endpoint is also exposed:

```
http://localhost:9113/metrics
```

### 4. Verify Monitoring Integration

Check that the ServiceMonitor was created:

```bash
kubectl get servicemonitor
```

You should see `webapp-servicemonitor` listed.

**Verify Prometheus is scraping the webapp:**

1. Open Prometheus: `http://localhost:9090`
2. Go to **Status** → **Target Health**
3. Look for `webapp-service` - all instances should show as "UP" with scrapeUrl pointing to port 9113

**Query NGINX Metrics:**

In Prometheus, try these queries:
- `nginx_http_requests_total` - Total HTTP requests per pod
- `nginx_connections_active` - Active connections
- `rate(nginx_http_requests_total[5m])` - Request rate over 5 minutes

**View in Grafana:**

1. Open Grafana: `http://localhost`
2. Go to **Explore** (compass icon)
3. Select "Prometheus" as the data source
4. Query: `nginx_http_requests_total`

You can also import NGINX-specific dashboards from [Grafana Dashboard Library](https://grafana.com/grafana/dashboards/).

**Generate Traffic for Testing:**

To generate traffic for testing metrics and dashboards:

```bash
# Generate a burst of requests (bash/zsh)
for i in {1..100}; do curl -s http://localhost:8080 > /dev/null; done

# Or for Windows PowerShell
1..100 | ForEach-Object { Invoke-WebRequest -Uri http://localhost:8080 -UseBasicParsing | Out-Null }

# Or continuous traffic (bash/zsh) - press Ctrl+C to stop
while true; do curl -s http://localhost:8080 > /dev/null; sleep 0.1; done
```

**Access the Chaos Monitoring Dashboard:**

A pre-configured dashboard for chaos engineering monitoring is automatically deployed with the webapp:

1. Open Grafana: `http://localhost`
2. Go to **Dashboards** (four squares icon)
3. Search for "Chaos Engineering - NGINX Monitoring"
4. The dashboard shows:
   - NGINX request rate and active connections
   - Pod health and restart count
   - Container CPU and memory usage
   - Disk I/O activity
   - Connection states

> **Note**: The dashboard is deployed as part of the webapp Helm chart in the monitoring namespace. If you don't see data immediately, generate some traffic: `for i in {1..50}; do curl -s http://localhost:8080 > /dev/null; done`

## Chaos Engineering with Chaos Mesh

Chaos Mesh is installed to test application resilience under various failure conditions. The Chaos Dashboard provides a visual interface for managing experiments.

### Install Chaos Mesh

Chaos Mesh is installed with the following commands:

```bash
# Add Chaos Mesh repository
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm repo update

# Create namespace
kubectl create namespace chaos-mesh

# Install Chaos Mesh with dashboard
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
  --set dashboard.create=true

# Expose dashboard as LoadBalancer
kubectl patch svc chaos-dashboard -n chaos-mesh -p '{"spec": {"type": "LoadBalancer"}}'
```

### Access Chaos Dashboard

The Chaos Mesh dashboard provides a web interface for creating and managing chaos experiments.

#### 1. Configure RBAC Authorization

Create a service account and get access token:

```bash
# Apply RBAC configuration
kubectl apply -f chaos-mesh-rbac.yaml

# Get the access token
kubectl describe secret chaos-mesh-viewer-token -n default | grep "token:" | awk '{print $2}'

# Or for Windows PowerShell
kubectl describe secret chaos-mesh-viewer-token -n default | Select-String "token:" | ForEach-Object { ($_ -split "\s+")[1] }
```

Copy the token output.

#### 2. Access Dashboard

1. Open: `http://localhost:2333`
2. Select **Token** authentication method
3. Paste the token from the previous step
4. Click **Submit**

You now have full access to create and manage chaos experiments through the web interface.

### Available Chaos Experiments

The `chaos-experiments/` directory contains various failure scenarios:

**Basic Experiments:**
- **pod-failure.yaml**: Kills one pod for 30 seconds
- **cpu-stress.yaml**: Applies 80% CPU stress to one pod for 60 seconds
- **memory-stress.yaml**: Consumes 64Mi memory on one pod for 60 seconds
- **disk-io-stress.yaml**: Applies mixed random I/O stress with 10ms delay for 60 seconds

**NGINX-Specific Experiments:**
- **nginx-container-kill.yaml**: Kills only the NGINX container (not the exporter)
- **nginx-disk-io-delay.yaml**: Adds 100ms latency to disk I/O operations
- **nginx-combined-stress.yaml**: Applies both CPU and memory stress to NGINX container

**Advanced Workflow:**
- **advanced-workflow.yaml**: Sequential chaos workflow with recovery periods

### Running Chaos Experiments

#### Option 1: Using kubectl

Apply an experiment directly:

```bash
# Run a pod failure experiment
kubectl apply -f chaos-experiments/pod-failure.yaml

# Check experiment status
kubectl get podchaos

# View experiment details
kubectl describe podchaos pod-failure-experiment

# Delete/stop the experiment
kubectl delete -f chaos-experiments/pod-failure.yaml
```

#### Option 2: Using Chaos Dashboard

1. Open Chaos Dashboard: `http://localhost:2333`
2. Click **New Experiment**
3. Choose experiment type (PodChaos, StressChaos, NetworkChaos, etc.)
4. Configure target pods, duration, and chaos parameters
5. Click **Submit** to start the experiment
6. Monitor in real-time from the dashboard

#### Example: Running Different Experiments

```bash
# CPU Stress Test
kubectl apply -f chaos-experiments/cpu-stress.yaml

# Memory Stress Test
kubectl apply -f chaos-experiments/memory-stress.yaml

# Disk I/O Stress
kubectl apply -f chaos-experiments/disk-io-stress.yaml

# NGINX Container Kill
kubectl apply -f chaos-experiments/nginx-container-kill.yaml

# NGINX Disk I/O Delay
kubectl apply -f chaos-experiments/nginx-disk-io-delay.yaml

# Combined CPU + Memory Stress
kubectl apply -f chaos-experiments/nginx-combined-stress.yaml

# Advanced Workflow (Sequential)
kubectl apply -f chaos-experiments/advanced-workflow.yaml

# View all active chaos experiments
kubectl get podchaos,stresschaos,iochaos,workflows

# Clean up all experiments
kubectl delete podchaos --all
kubectl delete stresschaos --all
kubectl delete iochaos --all
kubectl delete workflows --all
```

### Monitoring During Chaos Experiments

Monitor the impact of chaos experiments in real-time:

**1. Grafana Dashboard (Recommended):**
- Open: `http://localhost`
- Navigate to "Chaos Engineering - NGINX Monitoring" dashboard
- Watch metrics change in real-time:
  - Request rate drops during pod failures
  - CPU spikes during stress tests
  - Memory increases during memory stress
  - Disk I/O activity during operations
  - Connection counts fluctuate

**2. Chaos Dashboard:**
- Open: `http://localhost:2333`
- View experiment status and timeline
- See which pods are affected

**3. Prometheus Queries:**
- Open: `http://localhost:9090`
- Try these queries during experiments:
  ```promql
  # Request rate during chaos
  rate(nginx_http_requests_total[1m])
  
  # CPU usage
  sum(rate(container_cpu_usage_seconds_total{pod=~"webapp.*",id=~".+"}[1m])) by (pod) * 100
  
  # Memory usage
  sum(container_memory_working_set_bytes{pod=~"webapp.*",id=~".+"}) by (pod)
  
  # Disk I/O
  sum(rate(container_fs_writes_bytes_total{pod=~"webapp.*",id=~".+"}[1m])) by (pod)
  
  # Pod restarts
  kube_pod_container_status_restarts_total{pod=~"webapp.*"}
  ```

**4. Application Health:**
- Test webapp: `http://localhost:8080`
- Check if NGINX responds during chaos
- Measure response time degradation

**5. Pod Status:**
```bash
# Watch pod status in real-time
kubectl get pods -w

# Check pod events
kubectl get events --sort-by='.lastTimestamp' | grep webapp

# View container logs
kubectl logs -l app.kubernetes.io/name=webapp -c nginx --tail=50
```

### Expected Behaviors During Experiments

**Pod Failure:**
- One pod terminates and restarts
- Request rate drops temporarily
- Other pods handle the load
- Recovery time: ~30 seconds

**CPU Stress:**
- CPU usage spikes to 80%
- Response time may increase
- Pod remains running
- No restarts expected

**Memory Stress:**
- Memory usage increases by 64Mi
- May trigger OOM if limits are exceeded
- Pod may restart if memory exceeds limits

**Disk I/O Stress:**
- Disk read/write operations delayed by 10-100ms
- Application response time may increase
- Disk I/O metrics spike in dashboard
- Pod remains running
- No restarts expected

## Useful Commands

### Verification

```bash
# Check all pods are running
kubectl get pods --all-namespaces

# Verify monitoring stack
kubectl get pods -n monitoring
kubectl get svc -n monitoring

# Verify webapp
kubectl get pods -l app.kubernetes.io/name=webapp
kubectl get svc webapp-service
kubectl get servicemonitor

# Verify Chaos Mesh
kubectl get pods -n chaos-mesh
kubectl get svc -n chaos-mesh

# Test service connectivity
curl -I http://localhost          # Grafana
curl -I http://localhost:9090     # Prometheus  
curl -I http://localhost:8080     # Webapp
curl -I http://localhost:2333     # Chaos Dashboard
```

### Webapp Management

```bash
# Check application logs
kubectl logs -l app.kubernetes.io/name=webapp -c nginx

# Check exporter logs
kubectl logs -l app.kubernetes.io/name=webapp -c nginx-exporter

# Watch pod status
kubectl get pods -w

# Scale the application
kubectl scale deployment webapp --replicas=5

# Check service endpoints
kubectl get endpoints webapp-service

# View Helm release status
helm status webapp
```

### Chaos Mesh Commands

```bash
# Get Chaos Dashboard token
kubectl describe secret chaos-mesh-viewer-token -n default | grep "token:"

# List all chaos experiments
kubectl get podchaos,stresschaos,iochaos,workflows

# View chaos experiment details
kubectl describe podchaos pod-failure-experiment

# Delete specific experiment
kubectl delete podchaos pod-failure-experiment

# Delete all experiments
kubectl delete podchaos --all
kubectl delete stresschaos --all
kubectl delete iochaos --all

# Check Chaos Mesh status
kubectl get pods -n chaos-mesh
```

### Monitoring Commands

```bash
# Port forward to Grafana (if not using LoadBalancer)
kubectl port-forward -n monitoring svc/monitoring-grafana 3000:80

# Port forward to Prometheus
kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-prometheus 9090:9090

# Check Prometheus targets
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'

# Query NGINX metrics
curl -s http://localhost:9090/api/v1/query?query=nginx_http_requests_total | jq
```

## Cleanup

### Stop All Chaos Experiments

```bash
# Delete all running chaos experiments
kubectl delete podchaos --all
kubectl delete stresschaos --all
kubectl delete iochaos --all
kubectl delete workflows --all
```

### Uninstall the Web Application

```bash
# Uninstall webapp (this will also remove the dashboard from monitoring namespace)
helm uninstall webapp
```

### Uninstall Chaos Mesh

```bash
helm uninstall chaos-mesh -n chaos-mesh
kubectl delete namespace chaos-mesh
```

### Uninstall Monitoring Stack

```bash
# Uninstall monitoring (the webapp dashboard will be automatically removed when webapp is uninstalled)
helm uninstall monitoring -n monitoring
kubectl delete namespace monitoring
```

### Reset Kubernetes Cluster (Optional)

To completely reset the Kubernetes cluster:

1. Open Docker Desktop
2. Go to **Settings** → **Kubernetes**
3. Click **Reset Kubernetes Cluster**
4. Confirm the reset

### Disable Kubernetes (Optional)

To stop Kubernetes and free up resources:

1. Open Docker Desktop
2. Go to **Settings** → **Kubernetes**
3. Uncheck **Enable Kubernetes**
4. Click **Apply & Restart**

## Troubleshooting

### Kubernetes Not Starting in Docker Desktop

- Ensure Docker Desktop is running
- Check the status indicator in Docker Desktop UI
- Try restarting Docker Desktop
- Check Docker Desktop resources (Settings → Resources)
- Reset Kubernetes cluster if needed (Settings → Kubernetes → Reset)

### Cannot Access Service in Browser

- Verify the service exists: `kubectl get svc`
- Confirm EXTERNAL-IP shows `localhost` for LoadBalancer services
- Try accessing via NodePort: `http://localhost:<nodeport>`
- Confirm pods are running: `kubectl get pods`
- Check if another service is using the port (e.g., port 80 for Grafana, 8080 for webapp)
- Restart Docker Desktop if LoadBalancer isn't assigning localhost

### Pods Not Starting

- Check pod logs: `kubectl logs <pod-name>`
- Describe pod for events: `kubectl describe pod <pod-name>`
- Verify Docker Desktop has sufficient resources (Settings → Resources)
- Check Docker Desktop disk space
- Restart Docker Desktop

### kubectl Context Issues

- Check current context: `kubectl config current-context`
- Switch to docker-desktop: `kubectl config use-context docker-desktop`
- List available contexts: `kubectl config get-contexts`

### Helm Install Fails

- Verify Helm version: `helm version` (should be v3.x)
- Check if release already exists: `helm list`
- Review error messages carefully

## Architecture

### Web Application
- **Image**: nginx:1.27-alpine
- **Exporter**: nginx/nginx-prometheus-exporter:1.3.0 (sidecar)
- **Replicas**: 3 (configurable in values.yaml)
- **Service Type**: LoadBalancer
- **Port Mapping**: 
  - 8080 (external) → 80 (NGINX container)
  - 9113 (metrics) → 9113 (exporter container)
- **Health Checks**: Liveness and readiness probes configured
- **Monitoring**: ServiceMonitor enabled for Prometheus integration
- **Metrics Exposed**: 
  - NGINX: connection stats, request counts, upstream metrics
  - Container: CPU, memory, network from cAdvisor
  - Go runtime: exporter process metrics
- **Grafana Dashboard**: Chaos Engineering monitoring dashboard deployed to monitoring namespace
  - Automatically uses the default Prometheus datasource (no hardcoded UIDs)
  - Safe to destroy and recreate environments
  - Shows NGINX, pod health, CPU, memory, and disk I/O metrics
  - Network metrics not available in Docker Desktop Kubernetes

### Monitoring Stack
- **Prometheus**: LoadBalancer service on `http://localhost:9090`
  - Scrapes webapp metrics every 30 seconds
  - Scrapes Kubernetes metrics (kube-state-metrics)
  - 7-day retention policy
  - 5Gi persistent storage
- **Grafana**: LoadBalancer service on `http://localhost` (port 80)
  - Pre-configured Prometheus datasource
  - Custom chaos monitoring dashboard
  - Kubernetes cluster dashboards
- **AlertManager**: ClusterIP service (port forwarding required)
- **Kube-State-Metrics**: Kubernetes object metrics
- **Node Exporter**: Disabled (not compatible with Docker Desktop)

### Chaos Mesh
- **Dashboard**: LoadBalancer service on `http://localhost:2333`
- **Controller Manager**: 3 replicas for high availability
- **Chaos Daemon**: DaemonSet on each node
- **DNS Server**: Custom DNS for network chaos
- **Supported Chaos Types**:
  - PodChaos: pod-kill, pod-failure, container-kill
  - StressChaos: CPU and memory stress
  - IOChaos: disk I/O delays, mixed random operations
  - TimeChaos, DNSChaos, HTTPChaos, and more
- **Workflows**: Sequential or parallel chaos orchestration

### Service Access URLs
- **Webapp (NGINX)**: `http://localhost:8080`
- **Webapp Metrics**: `http://localhost:9113/metrics`
- **Grafana**: `http://localhost` (port 80)
- **Prometheus**: `http://localhost:9090`
- **Chaos Dashboard**: `http://localhost:2333`
- **AlertManager**: `kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-alertmanager 9093:9093`

## Additional Resources

- [Docker Desktop Documentation](https://docs.docker.com/desktop/)
- [Docker Desktop Kubernetes](https://docs.docker.com/desktop/kubernetes/)
- [Helm Documentation](https://helm.sh/docs/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Prometheus Operator](https://prometheus-operator.dev/)
- [Chaos Mesh Documentation](https://chaos-mesh.org/docs/)
- [Chaos Mesh GitHub](https://github.com/chaos-mesh/chaos-mesh)
- [NGINX Prometheus Exporter](https://github.com/nginxinc/nginx-prometheus-exporter)
- [Grafana Dashboards](https://grafana.com/grafana/dashboards/)
