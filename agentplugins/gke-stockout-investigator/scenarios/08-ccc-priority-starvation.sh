#!/usr/bin/env bash
#
# Rule G — priority list too granular to fit the pod.
#
# The ComputeClass lists eight narrow machine types, and the pod's requests exceed every
# one of them, so the chain is exhausted on the first scheduling attempt and
# `whenUnsatisfiable: DoNotScaleUp` keeps the pods Pending. No shape further down would
# have worked either: the list could never have satisfied this pod at any position.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SCENARIO_TITLE="Priority list whose every shape is smaller than the pod that selects it"
SCENARIO_RULE="SKILL.md Rule G — Priority List Too Long, or Too Granular to Fit the Pod"
SCENARIO_CONTROLLER="transactional-db"
SCENARIO_PODS=5

scenario_manifest() {
    cat <<YAML
apiVersion: cloud.google.com/v1
kind: ComputeClass
metadata:
  name: db-granular-class
  labels:
    scenario: 08-ccc-priority-starvation
spec:
  # Eight exact machineType entries, the largest of which is c3-highmem-8. The pod
  # below requests 14 vCPU, so no entry can host a single replica and the chain is
  # exhausted on the first scheduling attempt. Eight is inside the ~10-entry traversal
  # cap, so the length of the list is not the defect -- the sizing is.
  priorities:
    - machineType: c3-standard-4
    - machineType: c3-standard-8
    - machineType: c3-highmem-4
    - machineType: c3-highmem-8
    - machineType: c2d-standard-4
    - machineType: c2d-standard-8
    - machineType: c2d-highmem-4
    - machineType: c2d-highmem-8
  whenUnsatisfiable: DoNotScaleUp
  nodePoolAutoCreation:
    enabled: true
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: transactional-db
  labels:
    scenario: 08-ccc-priority-starvation
spec:
  replicas: 5
  selector:
    matchLabels: {app: transactional-db}
  template:
    metadata:
      labels:
        app: transactional-db
        scenario: 08-ccc-priority-starvation
    spec:
      nodeSelector:
        cloud.google.com/compute-class: db-granular-class
      # Larger than every shape in the list above, so each priority is tried and
      # rejected in turn and the walk restarts.
      containers:
        - name: db
          image: registry.k8s.io/pause:3.9
          resources:
            requests:
              cpu: "14"
              memory: 56Gi
            limits:
              cpu: "14"
              memory: 56Gi
YAML
}

scenario_reasons() {
    cat <<JSON
"rejectedMigs": [
  {
    "mig": {"name": "gke-${CLUSTER_NAME}-c3-std-4", "nodepool": "db-granular", "zone": "${ZONE_A}"},
    "reason": {
      "messageId": "no.scale.up.mig.failing.predicate",
      "parameters": ["Insufficient cpu"]
    }
  },
  {
    "mig": {"name": "gke-${CLUSTER_NAME}-c3-std-8", "nodepool": "db-granular", "zone": "${ZONE_A}"},
    "reason": {
      "messageId": "no.scale.up.mig.failing.predicate",
      "parameters": ["Insufficient cpu"]
    }
  },
  {
    "mig": {"name": "gke-${CLUSTER_NAME}-c2d-hm-8", "nodepool": "db-granular", "zone": "${ZONE_B}"},
    "reason": {
      "messageId": "no.scale.up.mig.failing.predicate",
      "parameters": ["Insufficient cpu"]
    }
  }
],
"napFailureReasons": [
  {
    "messageId": "no.scale.up.nap.pod.zonal.resource.pool.exhausted",
    "parameters": ["c3", "${ZONE_A}"]
  }
]
JSON
}

scenario_notes() {
    cat <<'TXT'
The 14 vCPU request exceeds every one of the eight listed shapes, so the diagnosis
has to say the list could never have satisfied this pod -- not that the autoscaler
looped. What distinguishes Rule G from Rule B here is the comparison between the pod's
requests and the largest rule, which a single alert does not show.

The expected proposal collapses the eight exact machineType entries into a couple of
machineFamily entries, letting node auto-creation size the node to the pod, and states
what the resulting fleet costs: a class that provisioned nothing now provisions one
node per replica.
TXT
}

scenario_main "$@"
