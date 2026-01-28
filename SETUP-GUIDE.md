# Complete Cleanup and Recreation Guide

This guide will help you completely tear down and recreate the entire Kubernetes lab from scratch.

## Complete Cleanup

Run these commands to remove everything:

```bash
# 1. Stop all chaos experiments
kubectl delete podchaos --all
kubectl delete stresschaos --all
kubectl delete iochaos --all
kubectl delete workflows --all

# 2. Uninstall all Helm releases
helm uninstall webapp || true
helm uninstall chaos-mesh -n chaos-mesh || true
helm uninstall monitoring -n monitoring || true

# 3. Delete all namespaces
kubectl delete namespace chaos-mesh || true
kubectl delete namespace monitoring || true

# 4. Verify everything is clean
kubectl get all
kubectl get namespaces

# 5. Optional: Reset Kubernetes cluster in Docker Desktop
# Docker Desktop → Settings → Kubernetes → Reset Kubernetes Cluster
```

## Fresh Installation

Follow these steps in order:

### Step 1: Prerequisites

Ensure you have:
- Docker Desktop running with Kubernetes enabled
- `kubectl` and `helm` installed
- At least 4 CPUs and 8GB RAM allocated to Docker Desktop

Verify:
```bash
kubectl config current-context  # Should show: docker-desktop
kubectl cluster-info
helm version
```

### Step 2: Add Helm Repositories

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm repo update
```

### Step 3: Install Monitoring Stack

```bash
# Create namespace
kubectl create namespace monitoring

# Install kube-prometheus-stack
helm install monitoring prometheus-community/kube-prometheus-stack \
  -f monitoring-values.yaml \
  -n monitoring

# Wait for all pods to be ready (takes 2-3 minutes)
kubectl get pods -n monitoring -w
# Press Ctrl+C when all pods show Running and 1/1 or 2/2

# Verify services are exposed
kubectl get svc -n monitoring
# Should see monitoring-grafana and monitoring-kube-prometheus-prometheus as LoadBalancer
```

**Expected pods in monitoring namespace:**
- alertmanager-monitoring-kube-prometheus-alertmanager-0 (2/2)
- monitoring-grafana-xxxxx (2/2)
- monitoring-kube-prometheus-operator-xxxxx (1/1)
- monitoring-kube-state-metrics-xxxxx (1/1)
- monitoring-prometheus-node-exporter-xxxxx (disabled - won't appear)
- prometheus-monitoring-kube-prometheus-prometheus-0 (2/2)

### Step 4: Access Monitoring Tools

Test access:
```bash
# Grafana (wait 30 seconds after pods are ready)
curl -I http://localhost
# Should return: HTTP/1.1 302 Found

# Prometheus
curl -I http://localhost:9090
# Should return: HTTP/1.1 200 OK
```

Login to Grafana:
- URL: http://localhost
- Username: admin
- Password: admin123

### Step 5: Install Web Application

```bash
# Install with monitoring and dashboard enabled
helm install webapp ./webapp-chart \
  --set serviceMonitor.enabled=true \
  --set grafanaDashboard.enabled=true

# Wait for pods
kubectl get pods -w
# Press Ctrl+C when all webapp pods show Running and 2/2

# Verify deployment
kubectl get deployments
kubectl get svc webapp-service
kubectl get servicemonitor
kubectl get configmap webapp-dashboard -n monitoring
```

**Expected resources:**
- 3 webapp pods (2/2 - nginx + exporter containers)
- webapp-service (LoadBalancer, localhost:8080)
- webapp-servicemonitor (ServiceMonitor CRD)
- webapp-dashboard (ConfigMap in monitoring namespace)

### Step 6: Verify Monitoring Integration

**Important:** Wait 30-60 seconds after webapp deployment for Prometheus to discover and start scraping the targets.

**Best Method - Use Prometheus Web UI:**

Open your browser: **http://localhost:9090/targets**

Search for "webapp" - you should see:
- **Job:** `serviceMonitor/default/webapp-servicemonitor/0`
- **3 endpoints** with State **"UP"** (one for each webapp pod)

**Alternative - Command Line Verification:**

```bash
# Method 1: Check target health (simple grep)
curl -s http://localhost:9090/api/v1/targets | grep -c '"job":"webapp-service"'
# Should output: 3 (three webapp pods being scraped)

# Method 2: Verify NGINX metrics are being collected
curl -s 'http://localhost:9090/api/v1/query?query=nginx_http_requests_total'
# Should return JSON with "status":"success" and 3 results

# Method 3: Test a specific metric value
curl -s 'http://localhost:9090/api/v1/query?query=nginx_connections_active' | grep -o '"value":\[[^]]*\]'
# Should show active connections for each pod
```

**What Success Looks Like:**
- 3 targets with health "up"
- Each target scraping from port 9113
- NGINX metrics (nginx_http_requests_total, nginx_connections_active) visible
- Metrics update every 30 seconds

**Troubleshooting - If No Targets Appear:**

```bash
# 1. Verify ServiceMonitor exists
kubectl get servicemonitor webapp-servicemonitor

# 2. Check ServiceMonitor labels (needs "release: monitoring")
kubectl get servicemonitor webapp-servicemonitor --show-labels

# 3. Verify service has matching labels
kubectl get svc webapp-service --show-labels

# 4. Check if pods are ready
kubectl get pods -l app.kubernetes.io/name=webapp

# 5. Test metrics endpoint directly
kubectl get pods -l app.kubernetes.io/name=webapp -o name | head -1 | xargs kubectl port-forward 9113:9113 &
curl http://localhost:9113/metrics | grep nginx_
# Press Ctrl+C and run: killall kubectl

# 6. Restart Prometheus if needed (last resort)
kubectl rollout restart statefulset prometheus-monitoring-kube-prometheus-prometheus -n monitoring
# Wait 1-2 minutes, then check targets again
```

### Step 7: Access Grafana Dashboard

1. Open http://localhost in your browser
2. Login with admin/admin123
3. Go to **Dashboards** (four squares icon on left)
4. Search for "Chaos Engineering - NGINX Monitoring"
5. Open the dashboard

If you see "No data":
```bash
# Generate traffic
for i in {1..100}; do curl -s http://localhost:8080 > /dev/null; done
# Refresh dashboard after 10 seconds
```

**Dashboard should show:**
- NGINX Request Rate (rate per second)
- Active Connections (should show 1 per pod)
- Running Pods (should show 3)
- Container Restarts (should show 0)
- Connection States (reading/writing/waiting)
- Container CPU Usage (percentage)
- Container Memory Usage (bytes)
- Disk I/O (read/write bytes per second)
- Total HTTP Requests (bar gauge)

### Step 8: Install Chaos Mesh (Optional)

```bash
# Create namespace
kubectl create namespace chaos-mesh

# Install Chaos Mesh
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
  --set dashboard.create=true

# Expose dashboard as LoadBalancer
kubectl patch svc chaos-dashboard -n chaos-mesh -p '{"spec": {"type": "LoadBalancer"}}'

# Wait for pods
kubectl get pods -n chaos-mesh -w
# Press Ctrl+C when all pods show Running
```

**Expected pods in chaos-mesh namespace:**
- chaos-controller-manager-xxxxx (3 replicas)
- chaos-daemon-xxxxx (DaemonSet - 1 per node)
- chaos-dashboard-xxxxx (1 replica)
- chaos-dns-server-xxxxx (1 replica)

### Step 9: Configure Chaos Mesh RBAC

Apply RBAC configuration and get access token:

```bash
# Apply RBAC resources
kubectl apply -f chaos-mesh-rbac.yaml

# Get access token
kubectl describe secret chaos-mesh-viewer-token -n default | grep "token:" | awk '{print $2}'

# Or for Windows PowerShell
kubectl describe secret chaos-mesh-viewer-token -n default | Select-String "token:" | ForEach-Object { ($_ -split "\s+")[1] }
```

Copy the token output - you'll need it to access the Chaos Dashboard.

### Step 10: Access Chaos Dashboard

1. Open http://localhost:2333
2. Select **Token** authentication method
3. Paste the token from Step 9
4. Click **Submit**

You should now have full access to the Chaos Dashboard.

Verify connectivity:
```bash
curl -I http://localhost:2333
# Should return: HTTP/1.1 200 OK
```

### Step 11: Generate Test Traffic (Optional)

To populate metrics and test the monitoring dashboard:

```bash
# Generate a burst of requests (bash/zsh)
for i in {1..100}; do curl -s http://localhost:8080 > /dev/null; done

# Or for Windows PowerShell
1..100 | ForEach-Object { Invoke-WebRequest -Uri http://localhost:8080 -UseBasicParsing | Out-Null }

# Or continuous traffic (bash/zsh) - press Ctrl+C to stop
while true; do curl -s http://localhost:8080 > /dev/null; sleep 0.1; done
```

### Step 12: Test Chaos Experiments

```bash
# List available experiments
ls -1 chaos-experiments/

# Run a simple CPU stress test
kubectl apply -f chaos-experiments/cpu-stress.yaml

# Watch the dashboard for changes
# Open: http://localhost → Chaos Engineering - NGINX Monitoring

# Check experiment status
kubectl get stresschaos

# After 60 seconds, the experiment auto-completes
# Clean up
kubectl delete stresschaos --all
```

**Available experiments:**
- `pod-failure.yaml` - Kills one pod
- `cpu-stress.yaml` - 80% CPU load on one pod
- `memory-stress.yaml` - Consumes 64Mi memory
- `disk-io-stress.yaml` - Mixed I/O operations with delay
- `nginx-container-kill.yaml` - Kills nginx container only
- `nginx-disk-io-delay.yaml` - Adds I/O latency
- `nginx-combined-stress.yaml` - CPU + memory stress
- `advanced-workflow.yaml` - Sequential chaos workflow

## Troubleshooting Common Issues

### Issue: Grafana shows "Connection refused"
**Solution:** Wait 30-60 seconds after pods are ready. LoadBalancer takes time to activate.

### Issue: Chaos Dashboard shows "Authorization Required"
**Solution:**
```bash
# Verify RBAC resources exist
kubectl get serviceaccount chaos-mesh-viewer -n default
kubectl get secret chaos-mesh-viewer-token -n default

# If missing, apply RBAC configuration
kubectl apply -f chaos-mesh-rbac.yaml

# Get new token
kubectl describe secret chaos-mesh-viewer-token -n default | grep "token:"
```

### Issue: Dashboard shows "No data"
**Solution:** 
```bash
# Generate traffic (bash/zsh)
for i in {1..100}; do curl -s http://localhost:8080 > /dev/null; done
# Wait 10 seconds and refresh dashboard
```

### Issue: Prometheus not scraping webapp
**Solution:**
```bash
# Check ServiceMonitor exists
kubectl get servicemonitor

# Check if pods have correct labels
kubectl get pods --show-labels | grep webapp

# Restart Prometheus
kubectl rollout restart statefulset prometheus-monitoring-kube-prometheus-prometheus -n monitoring
```

### Issue: Dashboard not appearing in Grafana
**Solution:**
```bash
# Check ConfigMap exists
kubectl get configmap webapp-dashboard -n monitoring

# Verify label
kubectl get configmap webapp-dashboard -n monitoring --show-labels | grep grafana_dashboard

# Restart Grafana
kubectl rollout restart deployment monitoring-grafana -n monitoring
```

### Issue: Chaos experiments fail with "CRD not found"
**Solution:**
```bash
# Verify Chaos Mesh CRDs are installed
kubectl get crd | grep chaos-mesh

# If missing, reinstall Chaos Mesh
helm uninstall chaos-mesh -n chaos-mesh
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
  --set dashboard.create=true
```

### Issue: Services stuck on "Pending" for LoadBalancer
**Solution:**
```bash
# Delete and recreate the service
kubectl delete svc webapp-service
helm upgrade webapp ./webapp-chart --set serviceMonitor.enabled=true --set grafanaDashboard.enabled=true

# Or restart Docker Desktop
```

## Validation Checklist

After installation, verify:

- [ ] Monitoring namespace exists with all pods Running
- [ ] Grafana accessible at http://localhost with admin/admin123
- [ ] Prometheus accessible at http://localhost:9090
- [ ] Webapp accessible at http://localhost:8080
- [ ] Metrics accessible at http://localhost:9113/metrics
- [ ] 3 webapp pods Running with 2/2 containers each
- [ ] ServiceMonitor exists: `kubectl get servicemonitor`
- [ ] Dashboard ConfigMap exists in monitoring namespace
- [ ] Prometheus shows webapp targets as "up"
- [ ] Grafana dashboard "Chaos Engineering - NGINX Monitoring" shows data
- [ ] Chaos Mesh RBAC configured and token retrieved (if using Chaos Mesh)
- [ ] Chaos Mesh pods Running (if installed)
- [ ] Chaos Dashboard accessible at http://localhost:2333 with token (if installed)

## Time Estimates

- Clean installation (without Chaos Mesh): 5-7 minutes
- Clean installation (with Chaos Mesh): 10-12 minutes
- Monitoring stack pods ready: 2-3 minutes
- Webapp deployment: 30-60 seconds
- Chaos Mesh deployment: 1-2 minutes
- RBAC configuration and token retrieval: 1 minute
- Grafana dashboard data visible: 10-30 seconds after generating traffic

## Next Steps

Once everything is verified:

1. Explore the Grafana dashboard
2. Generate continuous traffic (see Step 11 commands)
3. Run chaos experiments and observe the impact
4. Create your own custom dashboards
5. Modify chaos experiments for your use case

## Support

For issues:
1. Check pod logs: `kubectl logs <pod-name>`
2. Review README.md for detailed documentation
3. Verify Docker Desktop has sufficient resources
4. Use the Validation Checklist above to ensure all components are working
