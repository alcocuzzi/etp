# Complete Cleanup and Recreation Guide

This guide will help you completely tear down and recreate the entire Kubernetes lab from scratch.

## Complete Cleanup

Run these commands to remove everything:

```bash
# 1. Uninstall all Helm releases
helm uninstall webapp || true
helm uninstall monitoring -n monitoring || true

# 2. Delete namespaces
kubectl delete namespace monitoring || true

# 3. Remove metrics-server (optional)
kubectl delete -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml || true

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

### Step 8: Install Metrics Server

Required for HPA and the autoscaler experiment (`kubectl top` commands depend on it):

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml && \
kubectl patch deployment metrics-server -n kube-system --type='json' \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' && \
echo "metrics-server patched"

# Wait ~60 seconds, then verify
kubectl get deployment metrics-server -n kube-system
kubectl top nodes
```

**Expected output of `kubectl top nodes`:**
```
NAME             CPU(cores)   CPU%   MEMORY(bytes)   MEMORY%
docker-desktop   123m         3%     1234Mi          15%
```

### Step 9: Generate Test Traffic (Optional)

To populate metrics and verify the monitoring dashboard:

```bash
# Generate a burst of requests (bash/zsh)
for i in {1..100}; do curl -s http://localhost:8080 > /dev/null; done

# Or for Windows PowerShell
1..100 | ForEach-Object { Invoke-WebRequest -Uri http://localhost:8080 -UseBasicParsing | Out-Null }

# Or continuous traffic (bash/zsh) - press Ctrl+C to stop
while true; do curl -s http://localhost:8080 > /dev/null; sleep 0.1; done
```

> **Note:** If `http://localhost:8080` does not respond (Docker Desktop may intercept it), use port-forward:
> ```bash
> kubectl port-forward svc/webapp-service 18080:8080 &
> for i in {1..100}; do curl -s http://localhost:18080 > /dev/null; done
> ```

## Troubleshooting Common Issues

### Issue: Grafana shows "Connection refused"
**Solution:** Wait 30-60 seconds after pods are ready. LoadBalancer takes time to activate.

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

### Issue: Services stuck on "Pending" for LoadBalancer
**Solution:**
```bash
# Delete and recreate the service
kubectl delete svc webapp-service
helm upgrade webapp ./webapp-chart --set serviceMonitor.enabled=true --set grafanaDashboard.enabled=true

# Or restart Docker Desktop
```

### Issue: `kubectl top nodes` / `kubectl top pods` returns "metrics not available"
**Solution:** metrics-server is not installed or not ready. Apply and patch:
```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type='json' \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
# Wait 60 seconds then retry
kubectl top nodes
```

### Issue: HPA shows `<unknown>` for CPU/memory targets
**Solution:** metrics-server must be running and healthy (see above). Also verify the HPA resource requests are set:
```bash
kubectl describe hpa webapp-hpa
kubectl get deployment webapp -o yaml | grep -A5 resources
```

## Validation Checklist

After installation, verify:

- [ ] Monitoring namespace exists with all pods Running
- [ ] Grafana accessible at http://localhost with admin/admin123
- [ ] Prometheus accessible at http://localhost:9090
- [ ] Webapp accessible at http://localhost:8080 (or via port-forward on 18080)
- [ ] Metrics accessible at http://localhost:9113/metrics
- [ ] 3 webapp pods Running with 2/2 containers each
- [ ] ServiceMonitor exists: `kubectl get servicemonitor`
- [ ] Dashboard ConfigMap exists in monitoring namespace: `kubectl get configmap webapp-dashboard -n monitoring`
- [ ] Prometheus shows webapp targets as "up": http://localhost:9090/targets
- [ ] Grafana dashboard "Chaos Engineering - NGINX Monitoring" shows data after generating traffic
- [ ] metrics-server running: `kubectl get deployment metrics-server -n kube-system`
- [ ] `kubectl top nodes` returns resource usage

## Time Estimates

- Full installation: 5-8 minutes
- Monitoring stack pods ready: 2-3 minutes
- Webapp deployment: 30-60 seconds
- metrics-server ready: 30-60 seconds
- Grafana dashboard data visible: 10-30 seconds after generating traffic

## Next Steps

Once everything is verified:

1. Explore the Grafana dashboard at http://localhost
2. Generate continuous traffic (see Step 8 commands)
3. Set up the autoscaler experiment — see `autoscaler/README.md`
4. Create your own custom Grafana dashboards

## Support

For issues:
1. Check pod logs: `kubectl logs <pod-name>`
2. Review README.md for detailed documentation
3. Verify Docker Desktop has sufficient resources (Settings → Resources: 4+ CPUs, 8+ GB RAM)
4. Use the Validation Checklist above to ensure all components are working
