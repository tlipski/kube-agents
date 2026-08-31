<!-- disableFinding(LINK_RELATIVE_G3DOC) -->

# ComputeClass: Gotchas, CUDs & Constraints

## Common Traps

-   **`AnyBestEffort` Reservation:** Bypasses ComputeClass priorities and falls
    back to On-Demand at the GCE level. Avoid; use `Specific` affinity.
-   **Reservations are Zonal:** Pin zones via `reservations.specific[].zones`.
    `location.zones` (per-priority or `priorityDefaults.location`) collides with
    `Specific` — omit it; a policy-only `location.locationPolicy: BALANCED` is
    fine.
-   **Disk Generation:**
    -   **Gen 4** (`n4`, `c4`): Requires **Hyperdisk**.
    -   **Gen 3** (`c3`, `c3d`): Takes **either** PD or Hyperdisk. It is not
        Gen 4 — a rule written as "Gen 2 vs Gen 4" silently miscategorises it.
    -   **Gen 2** (`n2`, `c2`): Requires **Persistent Disk**.
    -   *Rule:* For stateful PV workloads, do NOT mix Gen 2 and Gen 4 priorities
        (fallback attach fails -> `ContainerCreating` trap).
    -   *Exception (GKE 1.35.3-gke.1290000+):* back data PVs with the built-in
        `dynamic-rwo` StorageClass (`type: dynamic` +
        `use-allowed-disk-topology: "true"`). The autoscaler reads disk
        requirements and scales up only compatible nodes, so mixing generations
        in `priorities[]` becomes safe. See
        [Asset: dynamic-rwo-storageclass.yaml](../assets/dynamic-rwo-storageclass.yaml).
    -   *Caveat:* `dynamic-rwo` only governs **newly provisioned** PVs. An
        **existing** PV already created as a fixed PD or Hyperdisk does **not**
        retroactively become flexible — migrate its data onto a
        `dynamic-rwo`-backed volume (snapshot/restore or app/DB-level copy;
        PD↔Hyperdisk is not an in-place conversion).
    -   *Reference:*
        [Asset: postgres-primary-compute-class.yaml](../assets/postgres-primary-compute-class.yaml)

-   **"Hyperdisk-compatible" is not one property.** Balanced, Throughput and
    Extreme have different support matrices, so a priority rule that is fine
    for one is a hard attach failure for another. Check the disk type the
    workload's StorageClass actually names before judging a family.
    -   **Balanced / Balanced HA:** broad. `n1` and `e2` support it only by
        allowlist (account team), so treat them as unavailable unless the
        project already has one.
    -   **Extreme:** `a3`, `a4`, `c3`, `c3d`, `c4`, `g4`, `h3`, `m1`, `m3`,
        `m4`, `n2` only. **`n4` does not support it** — recommending `n4` as a
        fallback for a `hyperdisk-extreme` workload reintroduces the defect.
    -   **Extreme has a vCPU floor**, and a bare `machineFamily` rule will
        happily provision a node below it: 60 vCPU in general, `c3` 88, `c3d`
        60, `c4`/`g4` 96, `c4a`/`c4d`/`m3`/`m4` 64, `m1` 80, `n2` 80. Pin the
        floor with `minCores` on the rule, or name explicit shapes.
    -   Extreme, ML and Throughput cannot be **boot** disks.
    -   *Source:*
        [Hyperdisk overview](https://docs.cloud.google.com/compute/docs/disks/hyperdisks),
        [About Hyperdisk Extreme](https://docs.cloud.google.com/compute/docs/disks/hd-types/hyperdisk-extreme).

-   **Machine types GKE will not provision at all.** A ComputeClass naming one
    of these never scales up — a permanent scheduling failure, not a capacity
    risk, and no fallback rule underneath it changes that. Bare-metal shapes
    are the trap, because the name looks like an ordinary large shape.
    -   `c4` and `c4d`: no `-metal` types, **including `-lssd-metal`**.
    -   `c3`: no `-metal` types under Autopilot or the cluster autoscaler.
    -   `x4`: the entire series. `z3`: `z3-highmem-192-highlssd-metal`.
    -   `m4`: all `m4-hypermem-*`. `c4n`: the `-lssd` types (Preview).
    -   `c4a` bare metal is the exception that works: `c4a-highmem-96-metal`
        from 1.35.3-gke.1389000+, and it must be named as a `machineType` —
        a `machineFamily: c4a` rule provisions ordinary VMs instead.
        `c4a-standard-96-metal` is not supported for auto-creation.
    -   *Source:*
        [About machine support with GKE clusters](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/machine-support).

## Provisioning Nuance

-   **DWS FlexStart:** Queued (~3 min). `maxRunDurationSeconds` doesn't help
    obtainability.
-   **ComputeClass ≠ Full Node API:** node pool auto-creation doesn't support
    every `gcloud node-pools create` flag. If missing, use a **Manual Pool**
    bound to the ComputeClass.

## CUDs vs. Reservations

-   **Committed Use Discounts (CUDs):**
    -   **Automatic Consumption:** GKE cluster autoscaler automatically consumes
        CUDs based on the machine family of the node provisioned. If you have an
        `n4` CUD, provisioning an `n4` VM automatically consumes the discount up
        to exhaustion.
    -   **No Configuration Required:** A ComputeClass does **not** need to be
        specifically configured to consume CUDs. Consumption is implicit.
    -   **Flexible CUDs (FlexCUDs):** Portable across most families (C3, N2,
        N4). The discount follows whichever family the ComputeClass provisions
        for the On-Demand floor.
-   **Reservations:**
    -   **Explicit Configuration Required:** Unlike CUDs, capacity reservations
        are **not** automatically consumed. They must be explicitly configured
        and targeted via the Node Pool API (for manual pools) or within the
        ComputeClass `reservations` block (for node pool auto-creation).

## System Configuration Allowlist

GKE allows only specific `sysctls` and `kubeletConfig` fields.

-   **Check CRD:** `kubectl describe crd computeclasses.cloud.google.com` for
    the authoritative allowlist.
-   **Symptoms:** Unsupported keys show up in `status.conditions`.
-   **Version Gating:** Many fields (e.g. `singleProcessOOMKill`) require 1.33+
    or 1.34+.

## Service Mesh / Networking Nuances

-   Nodes provisioned by ComputeClasses (especially via node pool auto-creation)
    must be compatible with existing network policies or service mesh (e.g.,
    Anthos/Istio) sidecar requirements.
-   Ensure any required taints or labels for mesh injection or network traffic
    routing are included in the `nodePoolConfig`. An intentional **dedication**
    taint is valid here; the only redundant one is
    `cloud.google.com/compute-class` — node pool auto-creation applies and
    auto-tolerates it, so don't re-add it. **Manual pools, by contrast, DO
    require it as label + taint to bind to the ComputeClass.** Note: a
    `nodePoolConfig.taints` key cannot contain the reserved `kubernetes.io`
    substring (GKE Warden rejects it).
