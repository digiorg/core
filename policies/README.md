# Policies

This directory contains policy-as-code configurations for platform governance.

## Structure

```
policies/
└── kyverno/
    ├── cluster-policies/   # ClusterPolicy resources (cluster-wide)
    └── policies/           # Policy resources (namespace-scoped)
```

## Kyverno Policies

### Cluster Policies

Cluster policies apply to all namespaces:

| Policy | Description |
|--------|-------------|
| `require-labels` | Enforce required labels (app, team, env) |
| `disallow-latest-tag` | Block `:latest` image tags |
| `require-requests-limits` | Enforce resource requests/limits |
| `restrict-image-registries` | Allow only approved registries |
| `require-probes` | Enforce readiness/liveness probes |
| `disallow-default-namespace` | Block deployments to default namespace |

### Policy Exceptions

For legitimate exceptions, use `PolicyException` resources:

```yaml
apiVersion: kyverno.io/v2alpha1
kind: PolicyException
metadata:
  name: allow-system-privileged
  namespace: kube-system
spec:
  exceptions:
    - policyName: disallow-privileged
      ruleNames:
        - require-non-privileged
  match:
    any:
      - resources:
          kinds:
            - Pod
          namespaces:
            - kube-system
```

## Adding a New Policy

1. Create the policy YAML in the appropriate directory
2. Include required annotations:
   ```yaml
   metadata:
     annotations:
       policies.kyverno.io/title: "Policy Title"
       policies.kyverno.io/description: "What this policy does"
       policies.kyverno.io/severity: medium  # low, medium, high
   ```
3. Test locally: `kyverno apply <policy> --resource <test-resource>`
4. Submit PR

## Compliance Reporting

Policy violations are collected by the Kyverno Policy Reporter and displayed in Grafana dashboards.
