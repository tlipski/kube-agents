# SOP: Fleet Stockout Prevention & Capacity Audit (Daily Governance)

**Purpose:** Sweep every managed GKE cluster and GCP region for capacity stockout vulnerabilities, fragile ComputeClass configurations, quota bottlenecks, and obtainability risks before workloads suffer scheduling outages. The question this audit answers for a platform admin is: _which workloads and compute classes on my fleet will fail to scale or encounter a capacity stockout during demand spikes or zonal hardware shortages?_ Output is this stream's single GitHub ledger issue, rewritten in place on every run, plus narrow remediation Pull Requests carrying generated manifests for the findings that get promoted.

**Cron:** id `stockout-prevention`, schedule `20 9 * * *` (daily 09:20 UTC). The id is a stable observability identifier and does not change.

**Data sources:** `kubectl` read verbs, `gcloud compute ...`, `gcloud container ...`, GCP reservations API (`gcloud compute reservations list`), Spot capacity advice APIs (`gcloud beta compute advice capacity`, `gcloud beta compute advice capacity-history`), and Cloud Logging autoscaler visibility logs (`container.googleapis.com/cluster-autoscaler-visibility`). **Nothing else** — no external blueprints, no manual assumptions. Every conclusion is derived from live cluster and cloud reads you performed in this run.

---

## Execution Checklist

### 0. Open the audit run

```bash
./skills/fleet-audit/scripts/audit_report.py start --audit stockout-prevention [--repo "<owner>/<repo>"]
```

If multiple repositories are registered in `$GITOPS_STATE_CONFIGMAP` (`managed_repos`), pass `--repo "<owner>/<repo>"` explicitly:

- **Interactive session:** If no `--repo` was specified, prompt the user to choose which repository to target before proceeding.
- **Scheduled / unattended cron:** Iterate over all repositories in `managed_repos` in sequence, executing the audit and running `audit_report.py start` and `audit_report.py finish` for each repository with `--repo "<owner>/<repo>"`.

Returns `{"issue": <int|null>, "repo":"org/repo", "workspace":"/opt/data/gitops/stockout-prevention/org__repo", "findings_path":"/opt/data/scratch/findings_stockout-prevention.json", "pending_remediation_requests":[…]}`. Keep `findings_path` and `workspace` from this call; you write into both.

- `workspace` is the GitOps clone `start` made for you. The audit pod does not begin life inside a checkout, so this is the only tree that exists, and every `remediation.path` in Step 4 is resolved against it — a manifest written elsewhere is one the harness cannot find.
- `issue` is this stream's open ledger issue, or `null` when it has none. Either way you never create it — `finish` owns that.
- `pending_remediation_requests` lists finding ids a repo writer asked for with a `/remediate` comment on the ledger. Write a manifest for each one while you inspect (Step 4), or the promotion fails for want of a file.
- `start` creates and resets no branch. There is no report branch.

The helper owns every `git`/`gh` operation and renders the ledger issue body and every remediation PR body — **never hand-write an issue or PR body, never run `git commit`, `git push`, `gh issue create`, `gh pr create`, or `gh issue comment` yourself.**

**Never comment on the ledger yourself.** `/remediate` is a human reviewer's instruction to this harness, not a step in the audit: an agent that posts it is authorizing its own pull request.

### 1. Enumerate the target fleet

```bash
gcloud container clusters list --format=json
```

- Target every cluster with `status == "RUNNING"`. Record `{name, location, project, checks_run}` into `scope.clusters`.
- Obtain per-cluster credentials into an isolated kubeconfig so clusters cannot bleed into each other:
  ```bash
  export KC="${HERMES_HOME:-/opt/data}/.kubeconfigs/kubeconfig_<project>_<cluster>_<location>.yaml"
  KUBECONFIG=$KC gcloud container clusters get-credentials <cluster> --location=<location> --project=<project>
  ```
- **`checks_run` is mandatory on every cluster,** and each entry is an object, never a bare string:
  ```json
  {
    "check": "ccc-missing-fallbacks",
    "command": "kubectl --context prod-usc1 get computeclasses -A -o yaml"
  }
  ```
  `check` is the backticked slug from the §3 heading that defines it — `ccc-missing-fallbacks`, `ccc-no-ondemand-floor`, and so on. `command` is the literal invocation you issued on that cluster for that check. It must name one of `kubectl`, `gcloud`, `gsutil`, `bq`, `helm`, or `curl`; anything under eight characters is rejected.
- **A check the cluster's shape rules out is declared in `checks_not_applicable`** with a specific reason:
  ```json
  {
    "check": "single-zone-nodepool",
    "reason": "Cluster is GKE Autopilot mode; node pool management is fully delegated to GKE."
  }
  ```

### 2. Collect capacity and workload state

Collect the live cluster definitions, GCP reservations, regional capacity metrics, and autoscaler visibility logs:

```bash
# 1. Dump ComputeClasses, Workloads, and StorageClasses
KUBECONFIG=$KC kubectl get computeclasses,deployments,statefulsets,storageclasses -A -o json > /opt/data/scratch/stockout_state_<cluster>.json

# 2. Inspect GCP Reservations (guaranteed capacity anchors)
gcloud compute reservations list --project=<project> --format=json > /opt/data/scratch/reservations_<project>.json

# 3. Inspect GCP Regional Quotas for the cluster's region (e.g. us-central1)
gcloud compute regions describe <region> --project=<project> --format="json(quotas)"

# 4. Check Spot Capacity & Preemption Advice History
gcloud beta compute advice capacity-history --region=<region> --instance-selection-machine-types="g2-standard-4,n4-standard-4,c3-standard-4" --size=1 --types=PREEMPTION,PRICE --format=json

# 5. Autoscaler Visibility Logs (Stage 1 Triage Query)
gcloud logging read 'log_id("container.googleapis.com/cluster-autoscaler-visibility") AND resource.labels.cluster_name="<cluster>" AND (jsonPayload.noDecisionStatus.noScaleUp:* OR jsonPayload.resultInfo.results.errorMsg:*)' --project=<project> --freshness=24h --limit=1000 --format="value(timestamp,resource.labels.cluster_name,jsonPayload.noDecisionStatus.noScaleUp.unhandledPodGroups[0].napFailureReasons[0].messageId)"
```

### 3. Checks

**Standard exclusions — apply to every check below:**

- **S1 — system namespace:** `kube-system`, `kube-public`, `kube-node-lease`, `gmp-system`, `gmp-public`, `gke-gmp-system`, `cnrm-system`, `configconnector-operator-system`, `krmapihosting-system`, `istio-system`, `asm-system`, `anthos-identity-service`, `gatekeeper-system`, `composer-system`, or any namespace matching `gke-*`, `gke-managed-*`, or `config-management-*`.
- **S2 — GKE-managed object:** carries `addonmanager.kubernetes.io/mode`.
- **S3 — operator-owned:** non-empty `metadata.ownerReferences`.
- **S4 — explicit opt-out:** carries `kubeagents.x-k8s.io/stockout-audit: exempt`.
- **S5 — not running:** `spec.replicas == 0`, or completed batch Jobs.

**Evidence discipline — applies to every check below.** Each check states an
**Impact** line. That line is a template to adapt to what you actually saw, not
a string to paste: publishing it unchanged is how a finding ends up asserting a
threshold its own evidence contradicts.

- **The excerpt must contain the fact the finding asserts.** If the title or
  impact names a count, a threshold, a machine type or a version, that value
  appears in `evidence.excerpt` — or the finding says something else. A check
  whose threshold is "more than 10 rules" does not fire on a class with eight,
  and must never publish ">10" over an excerpt showing eight.
- **One command, one object.** `evidence.command` is the command that produced
  the excerpt for **this** finding's `object`. Quoting ComputeClass `X` under a
  finding whose object is StatefulSet `Y` leaves a reviewer unable to reproduce
  either.
- **Two findings against the same object agree.** If a run reports two findings
  on one `(cluster, object)` and both ran the same command, the excerpts are
  identical, because they came from one read. Where they differ, one is wrong
  and the harness rejects the document.
- **Cite, or leave the mechanism out.** A causal claim, a numeric limit or a
  GKE version gate comes from the `Reference:` file the check names, quoted as
  that file states it — including the full `1.35.3-gke.1290000` style build
  qualifier, never a rounded `1.35.3+`. If the reference does not support the
  mechanism, say what is misconfigured and stop. "This class cannot attach the
  disk the workload asks for" is a complete finding; an invented reason
  underneath it is worse than none, because a reviewer cannot check it.
- **Do not diagnose from a name, label or annotation.** An object called
  `db-legacy-class`, or labelled `scenario: priority-starvation`, asserts a
  diagnosis that the object's spec may not support. Judge the spec. If a
  finding is not re-derivable with the names stripped, it came from the label.
- **Read the workload, not only the class.** A ComputeClass is judged against
  the workloads that select it — their `resources.requests`, their
  `nodeAffinity` and `topologySpreadConstraints`, and the StorageClass their
  volumes name. Step 2 already dumps all of it. Most of these checks are
  unanswerable from `priorities[]` alone.

#### 3.1 Lack of fallback machine families and dimension diversity (`ccc-missing-fallbacks`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-prioritization.md`
- **Command:** `kubectl --context <ctx> get computeclasses,deployments,statefulsets -A -o yaml` — the class **and** the workloads that select it. The Zone dimension below is a property of the workload, not of `priorities[]`, so a read of the ComputeClass alone cannot answer this check.
- **Flag when:** A `ComputeClass` has `priorities[]` pinned to a single machine family or varies fewer than 2 of the 4 core obtainability dimensions across its priority chain: Zone, Family, Capacity Model (Spot vs. On-Demand), and Machine Size (vCPU core count). **Zone is judged on the consuming workload, not on `priorities[]`:** a class with three families is still single-zone if every pod that selects it carries a `requiredDuringSchedulingIgnoredDuringExecution` `topology.kubernetes.io/zone` affinity or a `topologySpreadConstraints` with one permitted zone. Name that pin in the excerpt when it is what you found.
- **Do NOT flag:** ComputeClasses that vary 2+ obtainability dimensions (e.g. multi-zone `c3` fallback to `n4` and `n2`, or Spot fallback to On-Demand across zones); standard exclusions.
- **Also check `whenUnsatisfiable`.** `DoNotScaleUp` is the documented default (`compute-class-crd-fields.md`), so its presence is not by itself evidence of a deliberate choice — but it is what converts an exhausted priority chain into permanent `Pending`. `ScaleUpAnyway` is not a free safety valve: the same reference records that it provisions **E2** nodes on Standard, and `compute-class-debug.md` files that as a symptom in its own right. Propose it only where an E2 node can run the workload, and say in `recommendation.risk` that it will.
- **Severity:** `critical`. When GCE encounters a zonal shortage or stockout on that machine family, Cluster Autoscaler has no fallback path and scale-up fails completely.
- **Impact:** "This workload has only one <dimension> available to it (<value found>), so capacity exhaustion there causes scale-up to fail and leaves pods unschedulable." `<dimension>` is whichever of Zone, Family, Capacity Model or Machine Size your excerpt shows pinned, and `<value found>` is the value it is pinned to. Both come out of the excerpt; neither has a default.
- **Remediation:** `kind: manifest`. Fix the dimension the Impact names, not a different one. **Zone pinned on the workload:** relax the pod's `nodeAffinity`/`topologySpreadConstraints`, or set `location.zones` / `location.locationPolicy: BALANCED` on the class — adding families varies a dimension the failure does not run on and leaves the workload as exposed as it was. **Family pinned:** add secondary fallback families (e.g. `c3` to `n4` and `n2`), but check the family fits the workload first — a 2-vCPU pod under a `machineFamily: m1` rule provisions a 40-vCPU/961GB ultramem node, and adding fallbacks under that rule leaves the oversize as the preferred path. Where the pinned family is far larger than the pods need, the pin is the finding — recommend removing it.

#### 3.2 Spot-only ComputeClass without on-demand safety floor (`ccc-no-ondemand-floor`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-prioritization.md`, `skills/gke-compute-classes/references/compute-class-gotchas-and-cuds.md`
- **Command:** `kubectl --context <ctx> get computeclasses <name> -o yaml`
- **Flag when:** A ComputeClass `priorities[]` array contains only Spot instances (`spot: true` or `provisioningModel: SPOT`) with no On-Demand priority rule at the end, or a latency-sensitive inference workload is configured Spot-first.
- **Do NOT flag:** ComputeClasses that contain an On-Demand fallback priority at the bottom of `priorities[]`; workloads with explicit non-production/test opt-out.
- **Severity:** `major`.
- **Impact:** "If Spot VM capacity is preempted or exhausted in the region, the workload has no on-demand floor and remains permanently in Pending state."
- **Remediation:** `kind: manifest`. Append an On-Demand priority rule at the lowest priority in the ComputeClass manifest to act as a guaranteed capacity floor.

#### 3.3 Large VM shape scarcity (>32 vCPU) without multi-family fallbacks (`ccc-large-vm-scarcity`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-prioritization.md`, `skills/gke-compute-classes/references/compute-class-gotchas-and-cuds.md`
- **Command:** `kubectl --context <ctx> get deployments,statefulsets,computeclasses -n <ns> <name> -o yaml`
- **Flag when:** A workload or ComputeClass requests very large VM sizes (>32 vCPUs, such as `m1-ultramem-160`, `c3-highcpu-88`, `a2-highgpu-8g`) from thin capacity pools without secondary fallback families or horizontal replica spreading — **and the shape is one GKE will actually provision.** Check the name against §3.13 before you get here: a shape on that list is scarce in no sense, it simply does not exist, and it is §3.13's finding rather than this one's.
- **Do NOT flag:** Workloads requesting standard/horizontal shapes (<=32 vCPUs); stateful monolithic databases that explicitly declare multi-region failover; anything §3.13 covers.
- **Severity:** `major`.
- **Impact:** "<shape> is a very large VM shape (<n> vCPU), drawn from a thin regional capacity pool and prone to sudden stockouts during scale-up." Take `<shape>` and `<n>` from the excerpt.
- **Remediation:** `kind: manifest`. **Gate this on Pod requests first:** node auto-creation sizes nodes to Pod _requests_, so a single pod requesting >32 vCPU cannot land on a smaller node, and smaller-core fallbacks only help horizontally-scalable workloads whose pods bin-pack. Where the pods do bin-pack (verify with `kubectl top pod`), propose smaller replica shapes with horizontal autoscaling. For a genuinely large single pod, vary **zone and family** instead and say why size cannot vary — proposing `c3-highmem-88` under a pod that needs one 200-vCPU node is a fallback the autoscaler will never take.

#### 3.4 Excessive priority rules causing autoscaler backoff loops (`ccc-priority-starvation`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-prioritization.md`, `skills/gke-compute-classes/references/compute-class-debug.md`
- **Command:** `kubectl --context <ctx> get computeclasses <name> -o yaml`
- **Flag when:** A `ComputeClass` carries more than the **~10 priority entries** the traversal supports (the reference's cap, which exists to prevent infinite loops), so rules past it are never reached. Count the rules, put the count in the excerpt, and state the count you found rather than the threshold.
- **Separately, and only as `minor`:** a class that stays inside the cap but enumerates granular `machineType` rules where a `machineFamily` rule would do. GKE can then create nodes on any viable type in the series, which improves the odds of landing on the preferred configuration. This is a best-practice improvement, not a failure — there is no documented solver, cache, or permutation limit, and nothing below ~10 entries triggers a backoff loop. The real timing caveat the reference describes is different and is about **churn**: an unobtainable shape holds a 5-minute cooldown, and under heavy Pod create/delete the autoscaler may not reach later rules before earlier cooldowns expire. Where that is what you observed, cite the autoscaler visibility logs for it and consider disabling active migration rather than rewriting the priority list.
- **Do NOT flag:** ComputeClasses using <= 5 broad `machineFamily` level definitions (e.g. `n4`, `c3`, `n2`).
- **Severity:** `major` over the traversal cap; `minor` for granularity alone.
- **Impact:** over the cap, "Priority rules past the ~10-entry traversal cap are never evaluated, so the fallbacks below them cannot be reached." For granularity, say that the class forgoes viable machine types within each series — not that anything is starved.
- **Remediation:** `kind: manifest`. Compress granular rules to family level (`machineFamily`), **carrying the sizing the granular rules expressed**: a list of `c3-standard-4` through `c3-highmem-8` becomes `machineFamily: c3` plus `minCores`/`minMemoryGb`, or the boundary is lost and the autoscaler may size nodes anywhere in the series. Check the consuming workload's `resources.requests` before compressing — if no rule in the original list could host one replica, that is §3.12(e) and a far more serious finding than this one.

#### 3.5 Mixed disk generations on PV-attached ComputeClasses (`ccc-mixed-disk-generations`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-gotchas-and-cuds.md`, `skills/gke-compute-classes/references/compute-class-provisioning-methods.md`
- **Command:** `kubectl --context <ctx> get computeclasses,statefulsets -n <ns> <name> -o yaml`
- **Flag when:** A stateful workload using PersistentVolumes references a ComputeClass whose `priorities[]` mixes machine generations that cannot attach the same disk, causing PV attachment deadlocks upon failover. **Gen 2** (`n2`, `n2d`, `c2`) takes Persistent Disk only; **Gen 4** (`n4`, `c4`) takes Hyperdisk only; **Gen 3** (`c3`, `c3d`) takes either, so a chain of `n1`, `n2`, `c3` is not a mix.
- **Do NOT flag:** Stateless workloads; ComputeClasses whose priorities are purely Gen 2 or purely Gen 4/Hyperdisk-compatible; clusters on **GKE 1.35.3-gke.1290000 or later** whose data PVs already use the built-in `dynamic-rwo` StorageClass.
- **Severity:** `critical`.
- **Impact:** "Stateful PV workload mixes <generation A> and <generation B> machine families, which cannot attach the same disk type — volume attachment fails and deadlocks when scaling across nodes." Substitute the two generations your excerpt shows. There is no default pair to fall back on: a finding that still reads `<generation A>`, or that names a pair the excerpt does not contain, is wrong on the one fact it exists to report.
- **Remediation:** On **1.35.3-gke.1290000+** — read the cluster's actual version with `gcloud container clusters describe ... --format='value(currentMasterVersion)'` and quote it, rather than assuming the fleet is current — emit `kind: manifest` moving the data PVs to the built-in `dynamic-rwo` StorageClass (`type: dynamic` + `use-allowed-disk-topology: "true"`), so the autoscaler scales up only disk-compatible nodes and skips the incompatible-generation priority. On older versions, emit `kind: manual` to unify priorities. Three things the recommendation must carry: `volumeBindingMode: WaitForFirstConsumer`; that `dynamic-rwo` selects the disk type per node, so a class pinning a specific type is giving that pin up; and that only **newly provisioned** PVs are governed, so existing volumes need a migration.
- **A StatefulSet's `volumeClaimTemplates` is immutable.** Changing `storageClassName` there cannot be applied to a live StatefulSet — the API server rejects every update outside `replicas`, `ordinals`, `template`, `updateStrategy`, `revisionHistoryLimit`, `persistentVolumeClaimRetentionPolicy` and `minReadySeconds`. The fix is still the right file edit, but `recommendation.risk` must say it needs `kubectl delete statefulset --cascade=orphan` and a recreate (or a `Replace=true` sync), not a rolling update. See the §4 feasibility gate.

#### 3.6 Incompatible machine families for Hyperdisk workloads (`ccc-hyperdisk-incompatible`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-gotchas-and-cuds.md`, `skills/gke-compute-classes/references/compute-class-provisioning-methods.md`
- **Command:** `kubectl --context <ctx> get storageclasses,computeclasses,deployments -n <ns> -o yaml`
- **Read the StorageClass's `parameters.type` first, and judge against that type.** "Hyperdisk-compatible" is not one property — Balanced, Throughput and Extreme have different support matrices, and the gotchas reference carries them. A family that is fine for `hyperdisk-balanced` can be a hard attach failure for `hyperdisk-extreme`.
- **Flag when:** A workload using Hyperdisk storage uses a ComputeClass with a priority rule that cannot attach **that** disk type — including a rule that names a supported family but permits shapes below the type's vCPU floor.
- **Do NOT flag:** Workloads using standard Persistent Disk (`pd-standard`, `pd-ssd`); ComputeClasses whose every rule supports the disk type the workload names, at a size that clears its floor.
- **Severity:** `critical`.
- **Impact:** "Autoscaler fallback lands on a machine family that does not support the disk type this workload requests, causing node provisioning or pod volume attachment to fail."
- **Remediation:** `kind: manifest`. Replace the incompatible rules with families that support the workload's disk type, and **verify the replacement against the reference before writing it** — this check firing on the fix it just proposed is the failure mode. Two traps: `n4` supports Balanced but **not Extreme**, so it is not a valid `hyperdisk-extreme` fallback; and a bare `machineFamily: c3` rule permits `c3-standard-4`, far below the 88-vCPU floor Extreme needs on C3, so pin it with `minCores`.
- **Ask whether the disk tier is the mismatch.** Where the floor forces a node many times the pod's requests — an 8-vCPU database pulling an 88-vCPU node to satisfy `hyperdisk-extreme` — the cheaper, smaller change is usually the StorageClass, not the ComputeClass. Put both options in `recommendation.rationale` and say which you chose.

#### 3.7 Regional quota exhaustion risk across fleet (`quota-exhaustion-risk`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-gotchas-and-cuds.md`
- **Command:** `gcloud compute regions describe <region> --project=<project> --format="json(quotas)"`
- **Flag when:** Total requested GPU/TPU/CPU limits across all clusters in the same GCP project and region reach >=90% of the project's regional quota limit (e.g. demanding 32 L4 GPUs when regional quota is 24).
- **Do NOT flag:** Projects where regional quota limit exceeds total fleet workload demand with >= 25% headroom.
- **Severity:** `critical`.
- **Impact:** "Workload resource requests across fleet exceed regional GCP quota limits; Cluster Autoscaler cannot provision additional nodes even if physical capacity exists."
- **Remediation:** `kind: manifest`. Adjust workload request caps in GitOps manifests to fit strictly within quota limits, and submit a quota increase recommendation for the GCP project.

#### 3.8 High preemption risk or low obtainability on Spot instances (`spot-scarcity-risk`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-prioritization.md`
- **Command:** `gcloud beta compute advice capacity-history --region=<region> --instance-selection-machine-types="g2-standard-4,n4-standard-4,c3-standard-4" --size=1 --types=PREEMPTION,PRICE --format=json`
- **Flag when:** Workloads or ComputeClasses request Spot VM shapes that have high historical preemption rates (>20%) or low obtainability scores in `compute advice`, without alternative family fallbacks.
- **Do NOT flag:** Spot configurations that have high obtainability scores or comprehensive multi-family fallbacks; non-production environments.
- **Severity:** `major`.
- **Impact:** "Spot machine shapes have high historical preemption rates and severe obtainability constraints, putting workload uptime at extreme risk."
- **Remediation:** `kind: manifest`. Expand instance selection to include lower-preemption machine types and add secondary on-demand fallback priorities in GitOps.

#### 3.9 Single-zone node pools on Standard clusters (`single-zone-nodepool`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-provisioning-methods.md`
- **Command:** `gcloud container node-pools list --cluster=<cluster> --location=<location> --format=json`
- **Flag when:** A Standard mode GKE cluster has autoscaling node pools restricted to a single zone with no Node Auto-Provisioning (NAP) or regional multi-zone node pools configured, or node pools near `autoscaling.maxNodeCount` ceilings.
- **Do NOT flag:** Autopilot clusters (fully managed multi-zone); regional clusters with multi-zone node pools.
- **Severity:** `major`.
- **Impact:** "Node pool is locked to a single zone: any zonal stockout in that zone halts all cluster auto-scaling."
- **Remediation:** `kind: manifest`. Propose enabling multi-zone node pools or configuring Node Auto-Provisioning (NAP) in Terraform/Kustomize declarations.

#### 3.10 Reservation bypass, unreachable zones, or unallocated capacity mismatch (`reservation-mismatch-risk`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-gotchas-and-cuds.md`, `skills/gke-compute-classes/references/compute-class-debug.md`
- **Command:** `gcloud compute reservations list --project=<project> --format=json`
- **Flag when:** (a) A ComputeClass sets `reservations.affinity: AnyBestEffort/Automatic`, which silently bypasses ComputeClass priority chains and falls back to On-Demand at GCE layer; (b) A ComputeClass targets a specific reservation that does not exist or sits in an unreachable zone; or (c) Substantial reservation capacity is unallocated (`inUseCount << count`) while production workloads in the same region run unreserved. (Note: CUDs are financial commitments, not physical capacity reservations).
- **Do NOT flag:** ComputeClasses with valid targeted reservation bindings; non-production workloads.
- **Severity:** `critical` for broken/bypassed bindings, `major` for unallocated capacity mismatches.
- **Impact:** "ComputeClass fallback priorities are rendered inert by Automatic reservation affinity, or expensive guaranteed reservation capacity sits idle during stockouts."
- **Remediation:** `kind: manifest`. Target specific reservation names in GitOps manifests or update ComputeClass location to align with active reservations.

#### 3.11 Autoscaler out-of-resources leading indicators (`autoscaler-out-of-resources`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-debug.md`
- **Command:** `gcloud logging read 'log_id("container.googleapis.com/cluster-autoscaler-visibility") AND resource.labels.cluster_name="<cluster>" AND (jsonPayload.noDecisionStatus.noScaleUp:* OR jsonPayload.resultInfo.results.errorMsg:*)' --project=<project> --freshness=24h --limit=1000 --format="value(timestamp,resource.labels.cluster_name,jsonPayload.noDecisionStatus.noScaleUp.unhandledPodGroups[0].napFailureReasons[0].messageId)"`
- **Flag when:** Cluster autoscaler visibility logs emit `scale.up.error.out.of.resources`, `scale.up.error.quota.exceeded`, or `scale.up.error.ip.space.exhausted` within the past 24 hours, indicating that scale-up attempts failed before fallback recovery.
- **Do NOT flag:** Clusters with clean autoscaler visibility logs over 24h.
- **Severity:** `critical`.
- **Impact:** "Autoscaler has actively failed scale-up attempts due to physical cloud stockouts, quota exhaustion, or pod subnet IP exhaustion."
- **Remediation:**
  - `scale.up.error.out.of.resources`: If a `ComputeClass` manifest exists in the GitOps repo for the affected workload, `kind: manifest` (add secondary fallback machine families and multi-zone support). If no `ComputeClass` manifest exists in the GitOps clone (such as default Autopilot workloads), `kind: manual` (adopting a custom ComputeClass requires a workload-owner decision on which workloads adopt it and which families are acceptable; provide the recommended ComputeClass YAML definition with fallback priorities in `recommendation.action` verified against the §4 feasibility gate).
  - `scale.up.error.quota.exceeded`: `kind: manual` (request a regional/family GCP compute quota increase via Google Cloud Console or `gcloud compute project-info describe`).
  - `scale.up.error.ip.space.exhausted`: `kind: manual` (expand the VPC pod subnet secondary CIDR range).

#### 3.12 Dangling, unlabelled, or invalid ComputeClass configurations (`dangling-compute-class`)

- **Reference:** `skills/gke-compute-classes/references/compute-class-crd-fields.md`, `skills/gke-compute-classes/references/compute-class-debug.md`
- **Command:** `kubectl --context <ctx> get computeclasses,deployments,statefulsets -A -o yaml`
- **Flag when:** (a) A workload's `nodeSelector: cloud.google.com/compute-class` or namespace `cloud.google.com/default-compute-class` references a ComputeClass that does not exist; (b) A ComputeClass `status.conditions` reports invalid configuration; (c) `nodePoolAutoCreation.enabled` is false and referenced node pools lack `cloud.google.com/compute-class` label/taints; (d) A GPU workload references a ComputeClass without declaring `nvidia.com/gpu` tolerations; or (e) **no priority rule in the class can host one replica of a workload that selects it** — every `machineType` names a shape smaller than the pod's `resources.requests`, so the chain is exhausted on the first scheduling attempt.
- **(e) needs the workload dump, and it is the most common way a class is silently dead.** Compare each pod template's CPU and memory requests against every `machineType` rule; a `machineFamily` rule is sized to the pod by node auto-creation and so cannot fail this way. Five replicas requesting 14 vCPU against a list topping out at `c3-highmem-8` is permanent `Pending` for all five, with `whenUnsatisfiable: DoNotScaleUp` guaranteeing it. Put both numbers — the request and the largest rule — in the excerpt.
- **Do NOT flag:** Workloads referencing valid, reconciled ComputeClasses with matching node pool labels and tolerations.
- **Severity:** `critical`.
- **Impact:** name the case you found, not the list. (a) "References ComputeClass `<name>`, which does not exist." (b) "ComputeClass `<name>` reports `<condition>` and is not reconciled." (c) "Node pools backing `<name>` carry no matching label or taint." (d) "GPU workload declares no `nvidia.com/gpu` toleration." (e) "No rule in `<name>` can host one replica: the pod requests `<cpu>`/`<mem>` and the largest rule is `<shape>`." Each ends "— the workload stays permanently `Pending`."
- **Remediation:** `kind: manifest`. Correct the ComputeClass name in GitOps, fix invalid CRD fields, or add required GPU tolerations to workload templates. For (e), either raise the rules to shapes that fit the pod or compress them to `machineFamily` so auto-creation sizes the node — and say what the resulting fleet costs, because a class that provisioned nothing now provisions one node per replica.
- **Two things not to write here.** Updating a ComputeClass does **not** tear down or recreate existing node pools; GKE leaves existing nodes on their old configuration and applies the change to new ones, so a `risk` claiming disruption on re-apply is false. And on Autopilot there are no user-managed node pools to align, which makes (c) unreachable and its usual recommendation unactionable — on those clusters the cause is a stale auto-created pool still carrying the class's label after `priorities[]` changed. Say that instead.

#### 3.13 Machine type GKE will never provision (`ccc-invalid-machine-type`)

This is a separate check from §3.3 and not a severity of it. A shape on this list is not scarce, and no fallback rule underneath it would have helped — the two findings have different causes, different severities and different fixes, so deciding between them by reading a caveat inside §3.3 is how the wrong one gets published.

- **Reference:** `skills/gke-compute-classes/references/compute-class-gotchas-and-cuds.md`
- **Command:** `kubectl --context <ctx> get computeclasses -A -o yaml`
- **Flag when:** A `machineType` in `priorities[]` names a shape node auto-creation cannot provision, per the _Machine types GKE will not provision at all_ list in the reference: `-metal` on `c4`/`c4d` (including `-lssd-metal`), `c3` `-metal` under Autopilot or the autoscaler, all of `x4`, `z3-highmem-192-highlssd-metal`, all `m4-hypermem-*`, and `c4n` `-lssd`. **Match on the name, not the size** — bare-metal shapes read like ordinary large shapes, and this check is the only one that catches them.
- **Do NOT flag:** `c4a-highmem-96-metal`, which auto-creation does support from 1.35.3-gke.1389000+ when named as a `machineType` (a `machineFamily: c4a` rule provisions ordinary VMs instead); shapes that are merely large or merely scarce, which are §3.3.
- **Severity:** `critical`.
- **Impact:** "`<machineType>` is not a shape GKE node auto-creation can provision, in any region at any time, so this rule never yields a node and the workload stays permanently `Pending`." Take `<machineType>` from the excerpt.
- **Remediation:** `kind: manifest`. `recommendation.action` names the replacement shape, not the act of replacing one: for a bare-metal entry that is usually the equivalent VM in the same series, so `c4-highmem-288-lssd-metal` becomes `c4-highmem-288`. `recommendation.rationale` is "a fallback rule below this one would not have helped, because the rule above it never yields a node and nothing under it is reached."

### 4. Generate remediation artifacts

- Locate the existing declaration in the GitOps clone (`grep -rl "name: <object>" --include='*.yaml' <workspace>`).
- **The Declaration Rule**:
  - A remediation that changes an object that already exists must go to that object's **existing declaration in the GitOps repo**: locate it (`grep -rl "name: <object>" --include='*.yaml' .`), give that file's repo-relative path as `remediation.path`, and rewrite it as the object's complete desired manifest.
  - If the workload or ComputeClass has no declaration in the clone, the finding is `kind: manual`. Put the manifest you would have written in `recommendation.action`.
  - Never invent a new path for an object that is not declared in the repository.
- **Remediation Feasibility Gate**: Before proposing a new machine family or Spot tier, verify that:
  1. The target GCP zone actually offers the machine type (`gcloud compute machine-types list --zones=<zone>`).
  2. For On-Demand proposals, the project quota for that family (`N4_CPUS`, `C4_CPUS`, GPU types) is greater than 0 (`gcloud compute regions describe <region> --project=<project>`).
  3. For Spot proposals, the project's preemptible CPU quota (`PREEMPTIBLE_CPUS`) is greater than 0 (and `PREEMPTIBLE_LOCAL_SSD_GB` is greater than 0 if requesting local SSDs).
- Edit the manifest directly in `<workspace>`, adding the necessary fallback machine families, zones, or quota adjustments.
- **Then re-run the §3 checks against the file you just wrote.** A fix is a fixed point of this SOP or it is not a fix: the finding it targets must no longer fire, and **no other check may start firing**. Nothing here needs cluster access — you have the patched manifest and the state dump from Step 2, which is everything the checks read. The failure this catches is the common one: §3.6 fires on an `e2` fallback under a `hyperdisk-extreme` volume, and the proposed replacement is `n4`, which §3.6 rejects for exactly the same reason. Where the fix cannot clear every check, it is not ready — either write a different fix or degrade the finding to `kind: manual` and say what is unresolved.
- **Check the edit is applicable, with `git diff`.** The clone is the only mechanism available for this — the audit holds `roles/container.viewer`, so `kubectl apply --dry-run=server` is not reachable and never will be on these credentials. `git -C <workspace> diff -- <path>` names exactly the fields the fix changes; compare them against what the API server refuses to update in place:
  - **StatefulSet:** everything outside `replicas`, `ordinals`, `template`, `updateStrategy`, `revisionHistoryLimit`, `persistentVolumeClaimRetentionPolicy` and `minReadySeconds` — so any change under `volumeClaimTemplates`, including `storageClassName` and `resources.requests.storage`.
  - **Deployment / StatefulSet / DaemonSet / Job:** `spec.selector`. **Job:** `spec.template`. **Service:** `spec.clusterIP`. **PVC:** `spec.storageClassName`, and any decrease of `spec.resources.requests.storage`.

  A conflict does not make the fix wrong — the file edit is still correct GitOps — but `recommendation.risk` must then name the recreate the change needs (`kubectl delete <kind> <name> --cascade=orphan` and re-apply, or a `Replace=true` sync) instead of implying a rolling update. `finish` re-derives this from the same diff and appends a rollout note, so a `risk` line that already says it reads as deliberate rather than as a correction. `finish --dry-run` does **not**: it makes no git call, so the preview omits the note and cannot tell you whether your edit has this problem. Run the `git diff` yourself.

  Where a live schema check is worth having, `kubectl apply --dry-run=client --validate=strict -f <file>` does work on read-only credentials: it pulls the CRD's OpenAPI schema from the cluster over the discovery endpoint, so it catches a field name the installed ComputeClass CRD does not have. It validates the schema, not the admission path — it will not find any of the immutability conflicts above.

- **State the blast radius as numbers, not as prose.** `recommendation.risk` says what the resulting fleet becomes: node shapes and counts before and after, derived from the pod requests and the priority rules. "Slightly larger machine sizes" over a change that takes a workload from zero schedulable nodes to five 22-vCPU nodes is the understatement this rule exists to stop.
- **Mandatory Remediation Comments**: For every modified line in YAML, append an inline `# Remediation: <reason>` comment.
- Set `remediation.path` to the repo-relative file path, with `kind: manifest`.
- Reviewers may comment `/remediate <finding-id>` or `/remediate all` on the ledger issue to promote findings into PRs.

### 5. Emit findings.json

Write the schema exactly as the helper validates it to the `findings_path` returned in Step 0: `audit` set to `stockout-prevention`; `scope.clusters` non-empty, each entry carrying the mandatory `checks_run` list of `{check, command}` objects for the §3 checks that actually ran there; and for each finding, `check`, `severity`, `title`, `cluster`, `namespace`, `object`, `evidence.command`, `evidence.excerpt`, `impact`, `recommendation`, and `remediation`.

### 6. Close the audit run

```bash
./skills/fleet-audit/scripts/audit_report.py finish --audit stockout-prevention \
  --findings-file /opt/data/scratch/findings_stockout-prevention.json \
  [--repo "<owner>/<repo>"]
```

One JSON line comes back, carrying `status`, `issue_url`, `new`, `resolved`, `prs_opened`, `prs_closed`, `partial`, `coverage_gaps`, and `silent_ok`. Exit 2 means the validator rejected the document and nothing was published — fix the document, do not retry blind. Exit 1 is fatal. Exit 0 means it published.

`partial` is `true` when the run could not read the whole fleet: any cluster in `scope.skipped`, or any cluster kept in scope with a `limitations` note. `coverage_gaps` names each one in a sentence. The harness then refuses to draw conclusions from silence, because a workload or ComputeClass you never queried is not one that got resolved: `resolved` comes back `0` and no resolved-delta is posted, no remediation PR is retired as stale, and the ledger issue stays open even at zero findings — `status` is still `CLEAN`, but the issue survives with a comment naming what went unread. A check declared in `checks_not_applicable` is not a gap and does not raise the flag; it left the denominator. Nothing else raises it — it is `true` if and only if `coverage_gaps` is non-empty. A fleet big enough that the description had to drop findings is not a coverage gap: those workloads were queried, the title counts them, and the body says which ones it left out.

**`silent_ok` decides silence. Do not re-derive it.** `finish` returns `silent_ok: true` only when this run moved nothing an operator needs to hear about: nothing new, nothing resolved, no coverage gap, no remediation PR opened or closed. Read the flag rather than reassembling that from `status`, `new`, `resolved`, and `partial` yourself — that arithmetic is where a run talks itself into silence it has not earned. Two rules, and they are the whole rule:

- On a **scheduled** run, `silent_ok: true` → your entire final response is exactly `[SILENT]`. Otherwise report, and every report carries `issue_url` in full.
- **An on-demand run is never silent.** If a person dispatched this job — from a kanban card or straight from chat — someone is waiting on the answer, and `[SILENT]` throws it away. Report the outcome and the ledger URL whatever `silent_ok` says.

What to report in each case:

- `silent_ok: true` — `[SILENT]` on a scheduled run, nothing else and no preamble. On `CLEAN` the ledger issue closed as completed and every open remediation PR for this stream closed with it; on `UPDATED` the ledger was rewritten but nothing moved. Dispatched on demand, say which in one line and give the issue URL.
- `status: "CLEAN"` with `resolved: > 0` — every capacity gap this ledger tracked has been closed. Report the issue URL and the count.
- `status: "CLEAN"` with `partial: true` — nothing reproduced, but the ledger and its PRs stayed open because the coverage was incomplete. One line, the clean result plus the `coverage_gaps`, then stop.
- Any other outcome — reply with **one line**: counts by severity, new vs. resolved, skipped-cluster count if any, remediation PRs opened or closed, and the `issue_url`.

## Red Lines

- **Read-only against every cluster.** No `apply`, `patch`, `edit`, `delete`, `scale`, `drain`, `cordon`, or eviction.
- **No hand-written issue or PR bodies, and no direct git/gh calls.** `audit_report.py` owns the ledger issue, the remediation branches, the commits, and every body it renders.
- **No credentials in evidence.** A Secret's `data:` block, a token, or a private key never enters an excerpt; re-read with a projection that omits it.
- **A finding you cannot reproduce is dropped, not softened.** `evidence.command` is the literal command you executed; if the confirm read fails or the condition has cleared, the finding does not ship.
- **No fabricated numbers.** Resource quantities and machine families are either read off the live object or left to a human.
- **Stable ids or the delta lies.** An unstable id — one that varies between runs because the `object` it is derived from moved — turns one persistent problem into an infinite stream of "new" findings.
