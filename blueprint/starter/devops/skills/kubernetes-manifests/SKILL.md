---
name: kubernetes-manifests
description: Write Kubernetes manifests that set resource requests and limits, health probes, a restricted security
  context, network policies and disruption budgets. Use this whenever you create or change a Deployment,
  StatefulSet, Job, Service, Ingress, ConfigMap, Secret, NetworkPolicy, Helm chart or Kustomize overlay, deploy an
  application to Kubernetes, or review manifests for reliability or security, even for one small service, because
  the defaults run as root without limits.
license: MIT
metadata:
  sources: wshobson/plugins/kubernetes-operations/skills/k8s-manifest-generator, wshobson/plugins/kubernetes-operations/skills/k8s-security-policies
---

# Kubernetes manifests

Kubernetes' defaults are permissive: no resource limits, root allowed, every pod can reach every other pod. Each
manifest closes those gaps on purpose. Read how the project deploys first (plain manifests, Kustomize, Helm, a GitOps
tool) and write in that form.

## You write them, the cluster proves them

The agent usually has no access to the cluster. Write the manifests, validate them with what is available
(`kubeconform` or `kubectl apply --dry-run=client` when installed), and give the user the commands to check against
the cluster: `kubectl diff -f <dir>` before and `kubectl apply -f <dir>` when they agree. Applying is the user's
decision.

## 1. Labels

Use the recommended labels on every object, so tools and selectors agree: `app.kubernetes.io/name`,
`app.kubernetes.io/part-of`, and `app.kubernetes.io/version` where it helps. The selector of a Deployment cannot be
changed later, so keep it to the stable `name` label.

## 2. A Deployment in this shape

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orders-api
  labels:
    app.kubernetes.io/name: orders-api
    app.kubernetes.io/part-of: shop
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: orders-api
  template:
    metadata:
      labels:
        app.kubernetes.io/name: orders-api
        app.kubernetes.io/part-of: shop
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: api
          image: registry.example.com/orders-api@sha256:<digest>
          ports:
            - name: http
              containerPort: 8000
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              memory: 256Mi
          startupProbe:
            httpGet:
              path: /health/live
              port: http
            periodSeconds: 5
            failureThreshold: 30
          livenessProbe:
            httpGet:
              path: /health/live
              port: http
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /health/ready
              port: http
            periodSeconds: 5
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
```

## 3. Resources

- Set CPU and memory **requests** on every container; the scheduler places pods by them.
- Set a memory **limit**, equal to the request for predictable behaviour; a container over it is killed instead of
  starving its neighbours.
- A CPU limit throttles the container even when the node is idle. Leave it out unless you need a hard cap, and say
  why when you set one.
- Base the numbers on measured use, and revisit them after the first week.

## 4. Probes

- **Startup**: gives a slow start time before the other probes count.
- **Liveness**: "is the process stuck?". It checks only the process itself, never a database or another service;
  otherwise a database outage restarts every pod and makes it worse.
- **Readiness**: "can it take traffic now?". It may check dependencies; a failing pod is taken out of the Service
  without a restart.

## 5. Security context

The example meets the *restricted* Pod Security Standard: non-root with a numeric user, no privilege escalation, all
capabilities dropped, the default seccomp profile, a read-only root filesystem with `/tmp` as a writable volume.
Enforce it per namespace:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: shop
  labels:
    pod-security.kubernetes.io/enforce: restricted
```

`automountServiceAccountToken: false` keeps the API token out of pods that never call the Kubernetes API. A pod that
does gets its own service account with a Role that grants only the verbs and resources it uses.

## 6. Network policies

Start from deny-all in the namespace, then allow what each service needs. With a default deny on egress, allow DNS
too, or nothing resolves.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny
  namespace: shop
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
```

Network policies only work when the cluster's network plugin enforces them; ask the user when unsure.

## 7. Availability

- At least two replicas for anything users wait on.
- A PodDisruptionBudget, so node maintenance does not take all replicas at once:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: orders-api
  namespace: shop
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: orders-api
```

- Topology spread constraints or anti-affinity when replicas must be on different nodes or zones.

## 8. Configuration and secrets

- Configuration in a ConfigMap, referenced by the Deployment.
- A Secret object holds values only base64-encoded, which anyone can decode. Never commit one with values. Get
  secrets from the cluster's secret store integration (an external secrets operator, sealed secrets, the cloud's
  CSI driver) and commit only the reference.
- Images by digest, as in the example, so every pod runs the same build.

## Checklist

- [ ] Recommended labels; the selector on the stable name label only
- [ ] Requests on every container; a memory limit
- [ ] Startup, liveness (process only) and readiness probes
- [ ] Restricted security context; numeric non-root user; read-only root filesystem
- [ ] No service account token unless needed; a minimal Role when it is
- [ ] Default-deny network policy with explicit allows, DNS included
- [ ] Two or more replicas and a PodDisruptionBudget
- [ ] Image by digest; no Secret values in git

## In YAAP

- YAAP checkpoints each change by itself. When reshaping a set of manifests goes wrong, undo it with git
  (`git checkout -- <files>` through `run_command`), or ask the user to use *Restore to before this*.
- Record the cluster layout (namespaces, how deploys happen) in the project memory with `update_memory`.
