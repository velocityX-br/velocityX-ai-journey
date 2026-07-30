# Research Report: OpenStack Manila in SAP SCI / Converged Cloud (CCloud)
_Date: 2026-07-30 · Sources: SAP Wiki, sci-ai-mcp, Web_

## TL;DR
Manila is SCI's **Shared-File-System-as-a-Service** control plane, exposing NFS
(v3/v4) and CIFS/SMB shares backed by **NetApp** filers. It runs as Kubernetes pods
in the `monsoon3` namespace (SCI release **Epoxy**, `epoxy-20260622170629`), grouped
under the **Compute-Storage API** support group. Core upstream components
(manila-api / -scheduler / -share / -data) are augmented by SCI-specific nannies
(manila-ensure, manila-nanny, netapp-balance-nanny) and platform services (Castellum
autoscaling, Limes quotas). Storage/Manila ownership sits with the **GCID Storage Core
Team** (Maurice Escher, Chuan Miao, Sumit Arora).

## Key Findings (by requested dimension)

### 1. Environment Backgrounds (qa / prod / admin)
- **Namespace:** all Manila pods run in **`monsoon3`** in each region's control-plane
  cluster (e.g. `manila-share-netapp-ma01-md005-*`, `manila-nanny-*`). `[SCI]`
- **SCI release:** **Epoxy** (`epoxy-20260622170629`) — Epoxy = OpenStack **2025.1**,
  maintained upstream until ~Oct 2026. `[SCI][Web]`
- **NetApp cluster classes:** **ST** (Storage/Manila production), **MD** (Manila
  devices — QA-only), **aPOD** (admin POD). `[Wiki]`
- **Admin context:** ops via domain/project `ccadmin`/`cloud_admin` (same pattern as
  Nova/Neutron). `[Wiki][SCI]`
- **Backend host naming:** `manila-share-netapp-<filer>@<filer>#<aggregate>`. `[SCI]`

> TODO: 待验证 — per-region NetApp filer inventory and per-env Manila API endpoint
> hostnames are not published.

### 2. Service Introduction (one-liner)
Manila is SCI's **Shared File Systems** service (OpenStack project derived from
Cinder), delivering **NFSv3/v4 and CIFS/SMB** shares on **NetApp** storage, grouped
under **Compute-Storage API**. Consumers attach shares via share-networks (Neutron).
`[Wiki][SCI][Web]`

### 3. Key Contacts
| Role | Person / Handle |
|---|---|
| Manila service owner | **Maurice Escher** |
| Storage lead | **Sumit Arora** |
| Manila dev | **Chuan Miao** |
| Cinder owner (adjacent) | Walter Boring |
| On-call queue (SNOW) | **GCID Storage Core Team** (joint via Compute-Storage-API) |

- **Slack:** `#cc-os-manila` / `#manila`; storage escalation via Compute-Storage-API
  onduty. `[Wiki][SCI]`
- **Support group:** `compute-storage-api`. `[Wiki]`

### 4. Service Technical Architecture
- **Upstream core:** `manila-api` (REST entry), `manila-scheduler` (backend
  placement), `manila-share` (**one pod per NetApp filer host**), `manila-data`
  (data-copy / migration / backup). `[Web][SCI]`
- **SCI-specific:** **manila-ensure** (reconciles NetApp state), **manila-nanny**
  (share-sync + snapshot reconciliation containers), **netapp-balance-nanny**
  (aggregate rebalancing every ~2h), **Castellum** (share autoscaling). `[Wiki][SCI]`
- **NetApp object mapping:** share-server → **vserver (SVM)** `ma_<id>`; share
  instance → **FlexVol** `share_<id>`; access rule → **export-policy**. `[SCI]`
- **DHSS modes:** upstream `driver_handles_share_servers` True/False — SCI uses NetApp
  driver; share-networks tie to Neutron in DHSS=True. `[Web]`

### 5. Service Dependency
- **Hard/common deps:** **Keystone** (auth), **Neutron** (share-networks, DHSS=True),
  **MariaDB** (state), **RabbitMQ** (RPC). `[Web][SCI]`
- **Optional/adjacent:** **Nova + Cinder** (generic driver only — not the NetApp
  path), **Horizon** (UI), **Limes** (quotas), **Castellum** (autoscaling). `[SCI][Web]`
- **manila-csi-plugin:** Kubernetes CSI integration for consuming Manila shares. `[SCI]`

### 6. Key Repositories (docker image & helm chart)
- **Helm chart:** **`github.com/sapcc/helm-charts/tree/master/openstack/manila`**
  (+ `/templates/shares`); CSI plugin at `helm-charts/system/manila-csi-plugin`. `[SCI]`
- **Secrets:** `cc/secrets` — `manila.yaml`, `manila-vendor.yaml`; NetApp creds via
  **`NetappCredential`** + **Vault**. `[SCI]`
- **CI:** Concourse **`openstack-manila`** pipeline. `[Wiki]`
- **Upstream:** source `opendev.org/openstack/manila`; OSH chart image
  `quay.io/airshipit/manila:2026.1-ubuntu_noble`; Kolla via `enable_manila`. `[Web]`

> TODO: 待验证 — exact SCI Manila Kolla image name/tag/registry not published.

### 7. Lifecycle (deploy / upgrade / rollback)
- **Cadence:** 6-month named releases; **SLURP** enables skip-level upgrades. SCI on
  **Epoxy (2025.1)**; upstream current **2026.1 Gazpacho** (Maintained, rel.
  2026-04-01), **2025.2 Flamingo**, **2025.1 Epoxy**. `[Web][SCI]`
- **Recovery pattern (SCI norm):** delete unhealthy pod → Kubernetes recreates;
  restart share pods individually. `[SCI]` (inferred from Neutron/Nova pattern)

> TODO: 待验证 — no versioned SCI Manila deploy/upgrade/rollback runbook confirmed
> (only troubleshooting playbooks + generic delete-pod recovery).

### 8. Monitoring Setup
- **Nannies:** manila-nanny (share-sync / snapshot containers),
  netapp-balance-nanny (every ~2h). `[SCI]`
- **Alerts:** **`ManilaAvailabilityZoneUsageHigh`**, **`ManilaShareInodeUsageHigh`**;
  blackbox/synthetic probes in **cc3test**. `[SCI]`
- **Stack:** Prometheus + Grafana + Perses + Thanos + Supernova + Sentry; customer
  metrics via **Maia**. `[SCI][Wiki]`
- **Aggregate thresholds:** 75% warn / 90% crit. `[Wiki]`

> ⚠️ Wiki monitoring tables are empty placeholders; SCI alert names are the concrete
> source of truth.

### 9. Limitations
- **share-servers per filer:** ~**250 hard limit**. `[Wiki]`
- **Max share size:** **32 TiB** (raised from 20 TiB, Feb 2025). `[SCI]`
- **Shares per pool:** ~**1000**; default **4000 shares/project** quota. `[Wiki]`
- **Reserved capacity:** 50% reserved / over-subscription model. `[SCI]`
- **No alerting** for volume near-full on **ST** clusters. `[SCI]`
- **Aggregate capacity:** 75% warn / 90% crit. `[Wiki]`

## Source Comparison
| Aspect | SAP Wiki | sci-ai-mcp | Web / Community |
|--------|----------|------------|-----------------|
| Coverage | Ops handover, contacts, limits, cluster types | Live pod/config/version, NetApp mapping, alerts | Upstream arch, DHSS, releases |
| Key point | 250 share-servers/filer, GCOPSD authoritative | Epoxy `epoxy-20260622170629`, helm/secrets paths | manila-data role, DHSS True/False |
| Terminology | ST/MD/aPOD cluster classes | vserver/FlexVol/export-policy mapping | driver_handles_share_servers |
| Gaps | Empty monitoring tables, unwritten subpages | No versioned lifecycle runbook | NetApp docs unreachable, arch page ~2020 |

## Agreements & Conflicts
- ✅ All sources agree on core arch (api/scheduler/share) + NetApp backend + NFS/CIFS
  + Keystone/Neutron/DB/RabbitMQ deps.
- ✅ manila-data (data-copy/migration) corroborated by Web; SCI adds the nannies.
- ⚠️ **Version lag:** SCI on **Epoxy (2025.1)** vs upstream **Gazpacho (2026.1)**.
- ⚠️ **Cortex** is Nova-only; Manila's SCI-specific glue = manila-ensure/nanny +
  netapp-balance-nanny (NetApp-driven, not in upstream docs).
- ❓ Only SCI knows the concrete NetApp object mapping (vserver/FlexVol/export-policy).

## Gaps & Open Questions
1. Exact **SCI Manila Kolla image name/tag/registry**.
2. **Per-region NetApp filer inventory** + per-env Manila API endpoint hostnames.
3. Formal **versioned deploy/upgrade/rollback** runbook (only playbooks exist).
4. Wiki **monitoring tables are empty** — populate from SCI alert definitions.
5. **NetApp vendor docs** (docs.netapp.com) were unreachable — verify SVM/FlexVol
   limits independently.

## Sources
### SAP Wiki
- GCOPSD space — Manila handover pages (03/06/07 API Surface / Troubleshooting /
  Runbooks referenced), contacts, cluster-type & limits tables. (`wiki.one.int.sap`)
- DM_Storage space — storage ownership / NetApp cluster classes.
### sci-ai-mcp
- `cc/documentation-operation` — Manila pod/config, version, NetApp mapping, alerts,
  helm/secrets paths (operation-facing).
- `cc/documentation-customer` — share size limits, quota model, blogs.
### Web
- docs.openstack.org/manila — architecture, DHSS, share-networks.
- releases.openstack.org — Epoxy/Flamingo/Gazpacho lifecycle, SLURP.
- opendev.org/openstack/manila, openstack-helm manila chart (`quay.io/airshipit/manila`).
- ⚠️ docs.netapp.com — unreachable (404); NetApp specifics from SCI only.

**Companion (KB):** `SCI_Manila.md` (customer object model) + `SCI_Manila_ShareServer_Troubleshooting.md`.
