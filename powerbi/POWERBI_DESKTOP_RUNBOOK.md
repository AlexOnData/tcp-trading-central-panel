# PowerBI Desktop deployment runbook — TCP Trading Central Panel

Step-by-step guide for the user to publish the PowerBI model to PowerBI Service
using PowerBI Desktop. ~30-45 minutes total.

**Prerequisites already complete** (done by the deployment session):
- Azure SQL Database is live with 81,890 trades (`sql-tcp-prod-weu.database.windows.net` / `sqldb-tcp-prod-weu`)
- PowerBI workspace **"TCP - Trading Central Panel"** already exists (workspace id `0b2b53b6-25b5-46bd-b79c-7ed833ae2f4a`)
- PowerBI Service Principal `tcp-powerbi-sp` is workspace Admin
- SQL DB user `tcp-powerbi-sp` has `tcp_bi_reader` role
- SP OID `6d02d755-3e55-4afc-aab7-9ce4bcee04e1` is in `dim_UserRoles` with scope `admin`
- TMDL source files are at `powerbi/model/`
- `powerbi/TCP_TradingCentralPanel.pbip` placeholder created

---

## Phase 1 — Install PowerBI Desktop (~10 min, one-time)

1. Open **Microsoft Store** on Windows
2. Search "**Power BI Desktop**"
3. Click **Get** / **Install** — free, ~600 MB download
4. Wait for install to complete, then launch

Alternative: download MSI from https://www.microsoft.com/en-us/download/details.aspx?id=58494

---

## Phase 2 — Enable TMDL Preview feature (~2 min, one-time)

1. In PowerBI Desktop, click **File → Options and settings → Options**
2. Left panel: **Global → Preview features**
3. Check both:
   - ☑ **Power BI Project (.pbip) save option**
   - ☑ **Store semantic model using TMDL format**
4. Click **OK**, restart PowerBI Desktop (close and reopen)

---

## Phase 3 — Open the TMDL model (~5 min)

1. In PowerBI Desktop, click **File → Open report**
2. Navigate to `D:\TCP\TCP_TradingCentralPanel\powerbi\`
3. Select `TCP_TradingCentralPanel.pbip` — click **Open**
4. PBI Desktop should load the model from `powerbi/model/*.tmdl`
   - You'll see 15 tables in the Field List on the right
   - The `_Measures` table contains 69 DAX measures grouped by KPI family (Volume, PnL, Risk, etc.)

If you see "Failed to load model": there may be a TMDL syntax issue. Take a screenshot and share with Claude.

---

## Phase 4 — Configure SQL connection (~5 min)

The model has M parameters `SqlServer` and `SqlDatabase` that need to be set to actual values.

1. **Home → Transform data → Edit parameters**
2. Set values:
   - **SqlServer**: `sql-tcp-prod-weu.database.windows.net`
   - **SqlDatabase**: `sqldb-tcp-prod-weu`
3. Click **OK**
4. PBI Desktop will prompt for SQL credentials. Choose:
   - **Microsoft account** → sign in with `<your-account>@<your-tenant>.onmicrosoft.com`
   - Or **Database** → use any AAD account with `tcp_bi_reader` role on the DB
5. Click **Connect** — first connection will take 30-60s (Azure SQL cold start)

If you see "Data source unavailable" or 401: ensure your AAD account has access to the SQL DB. You can verify by running `sqlcmd -G -S sql-tcp-prod-weu.database.windows.net -d sqldb-tcp-prod-weu -Q "SELECT TOP 1 * FROM dim_Employees"` in PowerShell.

---

## Phase 5 — Refresh data (~5-10 min)

1. **Home → Refresh** (or press F5)
2. PBI Desktop will execute the M queries against Azure SQL and import data into the model
3. Watch the progress dialog — should download:
   - 81,890 rows in `v_trades_enriched`
   - ~10,200 rows in `v_employee_performance`
   - smaller counts in the other v_ tables
   - dim_Date, dim_Employees, etc. (small dim tables)
4. Once complete, model is fully populated

---

## Phase 6 — Save as `.pbix` (~2 min)

1. **File → Save as**
2. Path: `D:\TCP\TCP_TradingCentralPanel\powerbi\TCP_TradingCentralPanel.pbix`
3. Click **Save**
4. Wait for save — model file will be 5-50 MB depending on data compression

---

## Phase 7 — Publish to PowerBI Service (~2 min)

1. **Home → Publish**
2. Sign in with `<your-account>@<your-tenant>.onmicrosoft.com` if prompted
3. **Select destination**: choose workspace **"TCP - Trading Central Panel"**
4. Click **Select**
5. Wait for upload — 1-3 min depending on .pbix size
6. When successful, click "**Open in PowerBI**" or close the dialog

---

## Phase 8 — Configure dataset credentials in PowerBI Service (~3 min)

After publish, the dataset is in PowerBI but has no SQL credentials (Service can't refresh it).

1. Open [app.powerbi.com](https://app.powerbi.com)
2. Workspaces → **TCP - Trading Central Panel** → click on the dataset
3. **Settings** (gear icon, top right) → **Datasets** section → click on **TCP_TradingCentralPanel** dataset
4. Expand **Data source credentials**
5. Click **Edit credentials** for `Sql.Database(SqlServer, SqlDatabase)`
6. Authentication method: **OAuth2**
7. Sign in → use your `<your-account>` AAD account
8. Click **Sign in** and **Save**

---

## Phase 9 — Configure scheduled refresh (~2 min)

1. Same dataset settings page
2. **Scheduled refresh**: toggle **On**
3. **Refresh frequency**: Daily
4. **Time zone**: (UTC+02:00) Bucharest
5. **Time slots**: add `07:30 AM` (matches the Etapa 8 design — refreshes after the daily generator timer at 07:00 RO)
6. Click **Apply**

---

## Phase 10 — Publish to web for public URL (~2 min)

This generates a publicly-accessible URL anyone can view (anonymous, no login).

1. Workspaces → **TCP - Trading Central Panel** → click on the **report** (not the dataset)
2. **File → Embed report → Publish to web (public)**
3. Click **Create embed code**
4. Warning dialog: acknowledge that data will be publicly accessible (OK — data is synthetic Faker-generated, no real PII)
5. Copy the **public link** (format: `https://app.powerbi.com/view?r=<token>`)
6. Save the link somewhere — this is your portfolio-public PowerBI URL

---

## Done — Outcomes

- ✅ Dataset live in PowerBI Service with 81,890 trades
- ✅ Daily auto-refresh at 07:30 RO weekdays
- ✅ Public URL accessible by anyone (anonymous)
- ✅ Foundation for visual report design (drag-drop in PBI Desktop later)

## Troubleshooting

| Issue | Likely cause | Fix |
|---|---|---|
| "Failed to load .pbip" | TMDL preview not enabled | Phase 2 — re-check checkboxes, restart PBI Desktop |
| "Login failed" on SQL refresh | AAD account doesn't have access to DB | Check `sys.database_principals` for your AAD account; grant `tcp_bi_reader` role if missing |
| Refresh hangs | Azure SQL paused, cold-start in progress | Wait 60s and retry — first connection after auto-pause is slow |
| "Publish to web" disabled / grayed out | Tenant setting disabled | PowerBI Admin Portal → Tenant Settings → "Publish to web" — toggle Enabled |
| Workspace not visible in Publish dialog | Account not signed in OR SP-only workspace | Sign in with personal `<your-account>` account (not the SP) |
