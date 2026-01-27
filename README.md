# Kubernetes Lab with Docker Desktop

This repository contains Kubernetes resources for deploying a web application with monitoring and chaos engineering experiments using Docker Desktop's built-in Kubernetes cluster.

## Contents

- **webapp-chart/**: Helm chart for a simple echo web application
- **monitoring-values.yaml**: Configuration for Prometheus and Grafana monitoring stack
- **chaos-experiments/**: Chaos engineering experiments for testing resilience

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

## Deploying the Web Application

### Option 1: Deploy Using Helm (Recommended)

```bash
# Navigate to the repository directory
cd etp

# Install the webapp chart
helm install webapp ./webapp-chart

# Verify the deployment
kubectl get deployments
kubectl get pods
kubectl get services
```

### Option 2: Deploy with Monitoring Enabled

If you've already installed the monitoring stack (see section below), enable ServiceMonitor:

```bash
helm install webapp ./webapp-chart --set serviceMonitor.enabled=true
```

### Option 3: Deploy with Custom Values

```bash
# Create custom values file if needed
helm install webapp ./webapp-chart -f custom-values.yaml
```

## Accessing the Web Application

### 1. Check the Service Status

```bash
kubectl get svc
```

You should see output similar to:

```
NAME              TYPE           CLUSTER-IP      EXTERNAL-IP   PORT(S)          AGE
webapp-service    LoadBalancer   10.96.184.178   localhost     8080:30791/TCP   40s
```

> **Note**: Docker Desktop automatically maps LoadBalancer services to `localhost`.

### 2. Access the Application

With Docker Desktop, LoadBalancer services are accessible directly via `localhost`:

```
http://localhost:8080
```

Open your browser and navigate to the URL above. The application will respond with a message including the pod name.

### 3. Alternative: Using NodePort

You can also access the service using the NodePort:

```
http://localhost:30791
```

> **Note**: The NodePort (e.g., 30791) can be found in the PORT(S) column of `kubectl get svc`.

### 4. Port Forwarding Method

For more control, use port forwarding:

```bash
kubectl port-forward service/webapp-service 8080:8080
```

Then access at: `http://localhost:8080`

## Deploying Monitoring Stack (Prometheus & Grafana)

> **Important**: Install the monitoring stack BEFORE deploying the webapp with ServiceMonitor enabled, or the webapp installation will fail due to missing CRDs.

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

### 5. Enable Monitoring for Web Application (Optional)

If you already deployed the webapp, upgrade it to enable ServiceMonitor:

```bash
helm upgrade webapp ./webapp-chart --set serviceMonitor.enabled=true
```

Or if you haven't deployed it yet, deploy with monitoring enabled:

```bash
helm install webapp ./webapp-chart --set serviceMonitor.enabled=true
```

## Accessing Monitoring Tools

### Access Grafana

```bash
kubectl port-forward -n monitoring svc/monitoring-grafana 3000:80
```

Open your browser to: `http://localhost:3000`

**Default Credentials:**
- Username: `admin`
- Password: `admin123`

### Access Prometheus

```bash
kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-prometheus 9090:9090
```

Open your browser to: `http://localhost:9090`

### Access AlertManager

```bash
kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-alertmanager 9093:9093
```

Open your browser to: `http://localhost:9093`

## Chaos Engineering Experiments

The `chaos-experiments/` directory contains various chaos engineering scenarios:

- **pod-failure.yaml**: Simulates pod failures
- **cpu-stress.yaml**: Applies CPU stress to pods
- **memory-stress.yaml**: Applies memory stress to pods
- **network-delay.yaml**: Introduces network latency
- **advanced-workflow.yaml**: Complex chaos workflow

To run a chaos experiment (requires Chaos Mesh or similar):

```bash
kubectl apply -f chaos-experiments/pod-failure.yaml
```

## Useful Commands

### Check Application Logs

```bash
kubectl logs -l app=webapp
```

### Watch Pod Status

```bash
kubectl get pods -w
```

### Scale the Application

```bash
kubectl scale deployment webapp --replicas=5
```

### Check Service Endpoints

```bash
kubectl get endpoints webapp-service
```

### View Helm Release Status

```bash
helm status webapp
```

## Cleanup

### Uninstall the Web Application

```bash
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
- Confirm EXTERNAL-IP shows `localhost`: `kubectl get svc webapp-service`
- Try accessing via NodePort: `http://localhost:<nodeport>`
- Use port forwarding: `kubectl port-forward service/webapp-service 8080:8080`
- Confirm pods are running: `kubectl get pods`
- Check if another service is using port 8080

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

The web application uses:
- **Image**: hashicorp/http-echo:0.2.3
- **Replicas**: 3 (configurable in values.yaml)
- **Service Type**: LoadBalancer
- **Port Mapping**: 8080 (external) → 5678 (container)
- **Health Checks**: Liveness and readiness probes configured
- **Monitoring**: ServiceMonitor for Prometheus integration

## Additional Resources

- [Docker Desktop Documentation](https://docs.docker.com/desktop/)
- [Docker Desktop Kubernetes](https://docs.docker.com/desktop/kubernetes/)
- [Helm Documentation](https://helm.sh/docs/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Prometheus Operator](https://prometheus-operator.dev/)
