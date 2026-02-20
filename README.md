# Kubernetes Lab with Docker Desktop

This repository contains Kubernetes resources for deploying a web application with monitoring and autoscaling experiments using Docker Desktop's built-in Kubernetes cluster.

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

# 6. Install metrics-server (required for HPA)
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type='json' \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

**Access Points:**
- Grafana: http://localhost (admin/admin123)
- Prometheus: http://localhost:9090
- Webapp: http://localhost:8080 *(or via port-forward — see note below)*

> ⚠️ **Docker Desktop note:** The LoadBalancer on port 8080 may be intercepted by Docker's internal server. If `http://localhost:8080` returns a blank page or 404, use port-forward instead: `kubectl port-forward svc/webapp-service 18080:8080` and access via `http://localhost:18080`.

> 📘 **For complete cleanup and recreation instructions**, see [SETUP-GUIDE.md](SETUP-GUIDE.md)

## Contents

- **webapp-chart/**: Helm chart for an NGINX web application with Prometheus metrics
- **monitoring-values.yaml**: Configuration for Prometheus and Grafana monitoring stack
- **autoscaler/**: Python autoscaler comparing conventional HPA vs Ollama AI prediction

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed
- [kubectl](https://kubernetes.io/docs/tasks/tools/) installed (comes with Docker Desktop)
- [Helm](https://helm.sh/docs/intro/install/) installed (v3.x)
- Docker Desktop with Kubernetes enabled
- [Ollama](https://ollama.com/download) installed and running *(required for the autoscaler experiment — see `autoscaler/README.md`)*

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

**Install Metrics Server** (required for HPA / autoscaler)

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml && kubectl patch deployment metrics-server -n kube-system --type='json' -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' && echo "metrics-server patched"
```

**Access the NGINX Monitoring Dashboard:**

A pre-configured Grafana dashboard is automatically deployed with the webapp:

1. Open Grafana: `http://localhost`
2. Go to **Dashboards** (four squares icon)
3. Search for "Chaos Engineering - NGINX Monitoring"
4. The dashboard shows:
   - NGINX request rate and active connections
   - Pod health and restart count
   - Container CPU and memory usage
   - Disk I/O activity
   - Connection states

> **Note**: If you don't see data immediately, generate some traffic: `for i in {1..50}; do curl -s http://localhost:8080 > /dev/null; done`

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

# Verify metrics-server
kubectl get deployment metrics-server -n kube-system
kubectl top nodes

# Test service connectivity
curl -I http://localhost          # Grafana
curl -I http://localhost:9090     # Prometheus
curl -I http://localhost:8080     # Webapp (or use port-forward)
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

### Monitoring Commands

```bash
# Port forward to Grafana (if not using LoadBalancer)
kubectl port-forward -n monitoring svc/monitoring-grafana 3000:80

# Port forward to Prometheus
kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-prometheus 9090:9090

# Check Prometheus targets
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'

# Query NGINX metrics
curl -s 'http://localhost:9090/api/v1/query?query=nginx_http_requests_total' | jq

# Useful Prometheus queries
curl -s 'http://localhost:9090/api/v1/query?query=sum(rate(container_cpu_usage_seconds_total{pod=~"webapp.*"}[1m]))by(pod)*100' | jq
curl -s 'http://localhost:9090/api/v1/query?query=sum(container_memory_working_set_bytes{pod=~"webapp.*"})by(pod)' | jq
```

## Cleanup

### Uninstall the Web Application

```bash
# Uninstall webapp (also removes the dashboard ConfigMap from monitoring namespace)
helm uninstall webapp
```

### Uninstall Monitoring Stack

```bash
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
- **Grafana Dashboard**: NGINX monitoring dashboard deployed to monitoring namespace
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
  - Custom NGINX monitoring dashboard
  - Kubernetes cluster dashboards
- **AlertManager**: ClusterIP service (port forwarding required)
- **Kube-State-Metrics**: Kubernetes object metrics
- **Node Exporter**: Disabled (not compatible with Docker Desktop)
- **Metrics Server**: Installed with `--kubelet-insecure-tls` patch for Docker Desktop (required for HPA and `kubectl top`)

### Service Access URLs
- **Webapp (NGINX)**: `http://localhost:8080` or `kubectl port-forward svc/webapp-service 18080:8080` → `http://localhost:18080`
- **Webapp Metrics**: `http://localhost:9113/metrics`
- **Grafana**: `http://localhost` (port 80)
- **Prometheus**: `http://localhost:9090`
- **AlertManager**: `kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-alertmanager 9093:9093`

## Additional Resources

- [Docker Desktop Documentation](https://docs.docker.com/desktop/)
- [Docker Desktop Kubernetes](https://docs.docker.com/desktop/kubernetes/)
- [Helm Documentation](https://helm.sh/docs/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Prometheus Operator](https://prometheus-operator.dev/)
- [NGINX Prometheus Exporter](https://github.com/nginxinc/nginx-prometheus-exporter)
- [Grafana Dashboards](https://grafana.com/grafana/dashboards/)
- [Kubernetes HPA Documentation](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [metrics-server](https://github.com/kubernetes-sigs/metrics-server)
