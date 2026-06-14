# ADR-004: BACPAC export schedule and ownership

- **Status**: Accepted
- **Date**: 2026-05-15
- **Stage**: Etapa 1 (design), implemented in Etapa 4 (infra) and Etapa 5 (Function App)

## Context

The disaster-recovery layer for TCP combines two mechanisms:

- **Azure SQL Free Offer Point-in-Time Restore (PITR)** — 7 days of automatic backups maintained by the platform. RPO ≤ 1 h, RTO ≤ 30 min for catastrophic corruption / accidental DELETE inside the retention window.
- **Weekly BACPAC export to Blob Storage** — full schema + data export retained 28 days (lifecycle rule). Covers the longer recovery window and gives the project an exportable snapshot for archival, regression testing, and the academic-thesis demo (a self-contained `.bacpac` file the committee can inspect).

The pass-1 reviews surfaced two conflicting designs for the BACPAC export:

- `02_database_design.md` §12 (original) — **GitHub Actions workflow running every Sunday at 02:00 UTC**.
- `03_architecture.md` §11 (original) — **Function App `TimerTrigger_BacpacExport` running every Sunday at 08:00 RO**.

`review_holistic_pass1.md` CR-03 flagged this directly: only one mechanism can own the schedule, and the rest of the project (RBAC matrix, KV secrets, Storage container, observability) was already wired for the Function App timer path.

This ADR pins the decision and clarifies the operational contract so Etapa 4 IaC can implement it without re-deciding.

## Decision

**The BACPAC export is owned by the Function App.** A new timer trigger `TimerTrigger_BacpacExport` runs on NCRONTAB `0 0 8 * * 0` (Sunday 08:00 Europe/Bucharest, DST-safe via `WEBSITE_TIME_ZONE`).

### Why the Function App timer wins over GitHub Actions

- **Identity**: the Function App's Managed Identity already exists and is granted `Storage Blob Data Contributor` on the `bacpac-exports` container (via Bicep). A GHA-owned path would require a second service principal with the same role assignment, doubling the identity surface.
- **Cost**: GitHub Actions on the user's free account has a finite minutes budget (2 000 min/month for private repos on the Free plan). A weekly job is cheap, but the Function App timer is **free** (1M executions/month included; weekly = 4.3 per month).
- **Observability**: keeping the trigger inside the Function App routes its logs and metrics to the same Application Insights workspace as the daily generator. One KQL pane covers all maintenance jobs.
- **Failure handling**: the timer trigger can retry inside the same process and emit `tcp.bacpac.duration_ms`, `tcp.bacpac.size_bytes`, and `tcp.bacpac.status` custom metrics; the §12 observability dashboard then alerts when a Sunday slot produces no event.
- **No external authentication**: the MI calls `New-AzSqlDatabaseExport` against the Azure REST API directly, no PowerShell module install needed in a GHA runner.

### Why Sunday 08:00 RO (and not 02:00 UTC)

- **Database is warm**: 08:00 RO is after the morning warm-up trigger (06:55 RO weekdays) — though Sunday is a weekend day where the SQL DB has been auto-paused since the previous business-day close. The BACPAC export tolerates a 30–60 s SQL resume; the operation itself runs minutes, so the warm-up cost is a small fraction of total runtime.
- **Operationally observable**: a 08:00 RO trigger lands within working hours for the (Europe/Bucharest-based) project owner, so a Sunday-morning failure is noticed within hours, not the following Monday morning.
- **DST-safe**: `WEBSITE_TIME_ZONE=E. Europe Standard Time` ensures the trigger fires at 08:00 RO across the EET → EEST and EEST → EET boundaries.

### Implementation contract

1. **Trigger**: `TimerTrigger_BacpacExport`, NCRONTAB `0 0 8 * * 0`.
2. **Identity**: Function App system-assigned MI.
3. **RBAC required** (assigned by Bicep in Etapa 4):
   - `SQL DB Contributor` at the database scope (to invoke `New-AzSqlDatabaseExport`).
   - `Storage Blob Data Contributor` on the `bacpac-exports` container.
4. **Action**: POST to `https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{server}/databases/{db}/export?api-version=2023-08-01-preview` with body containing the target storage URI, the SAS or `AccessKey` payload (preferred: storage account key via KV reference; the `Export` API does not yet support full MI-to-Storage chaining), and `administratorLogin` / `administratorLoginPassword` for the source (one-time SQL admin still required by the Export API — flagged below).
5. **Polling loop**: the Export API returns an async operation URL (`Location` header). The trigger polls every 10 s for up to 30 min; emits `tcp.bacpac.duration_ms` once complete.
6. **Output naming**: `bacpac-exports/tcp-{YYYYMMDD}.bacpac` where `YYYYMMDD` is the export date in Europe/Bucharest.
7. **Lifecycle**: Storage Account management policy `daysAfterModificationGreaterThan: 28` deletes BACPACs older than 4 weeks.
8. **Failure alert**: a Kusto query in §12 ("BACPAC missed last Sunday") fires an App Insights alert if no `tcp.bacpac.status='succeeded'` event lands inside a 48-hour window after the expected slot.

### Open caveat: the Export API still wants SQL admin credentials

As of API version `2023-08-01-preview`, the `Export` action requires `administratorLogin` + `administratorLoginPassword` for the source database — even when the calling identity is MI. This conflicts with the AAD-only flip in Etapa 2 (per `02_database_design.md` §10.1).

**Resolution**: keep the `SQL-ADMIN-PASSWORD-BOOTSTRAP` secret in Key Vault **even after the AAD-only flip**, but renamed to `SQL-ADMIN-PASSWORD-EXPORT` and excluded from the "delete after bootstrap" cleanup step. The Function App reads it via Key Vault reference and forwards to the Export API. The SQL server keeps SQL-auth disabled (the admin password is no longer accepted for interactive logins), but the Azure-managed Export action accepts it via the control plane.

Document the residual surface honestly: this is a known platform constraint, not a security defect. The credential is rotated annually and is never used for an interactive connection. If a future API version supports MI-only export, drop the secret and update this ADR (status → Superseded).

## Consequences

- **`02_database_design.md` §12** now states: "Weekly BACPAC export to Blob Storage via `TimerTrigger_BacpacExport` in the Function App; see `docs/decisions/ADR-004-bacpac-export-schedule.md`."
- **`03_architecture.md` §11** retains the timer description and references this ADR.
- **`architecture.mmd`** shows the `BacpacExport` trigger node (added in the fix pass) with edges to SQL (control-plane Export API) and Storage Account (BACPAC blob write).
- **Secrets table** (`03_architecture.md` §7) gains a row for `SQL-ADMIN-PASSWORD-EXPORT` (renamed from `…-BOOTSTRAP`).
- **CI gate** (`03_architecture.md` §17.1) gains: "On the first Sunday post-deploy, a `tcp-YYYYMMDD.bacpac` blob exists in `bacpac-exports/`" (verified by a Day-7 manual check until the first Sunday has passed).

## Alternatives rejected

- **GitHub Actions weekly cron**: rejected for the cost/identity reasons above.
- **`sqlpackage` from a Function** (instead of REST `Export`): the binary is 200 MB, breaks the Y1 Consumption plan size limit, and the deployment story is more brittle.
- **Drop BACPAC entirely, rely only on PITR**: PITR is 7 days; for a thesis project that may sit dormant for weeks between defenses, that retention is insufficient for the demo-snapshot requirement.

## References

- `docs/design/02_database_design.md` §12 (Backups & DR).
- `docs/design/03_architecture.md` §11 (Disaster recovery).
- `docs/design/reviews/review_holistic_pass1.md` CR-03 (the originating conflict).
- `docs/design/reviews/review_arch_pass1.md` MJ-05 (the trigger-promotion finding).
