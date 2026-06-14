# Source artifact inventory

Inventory of read-only reference sources kept at the repo root. Captured during Etapa 0; used as input for stages that build derived artifacts (E2/E3 schema and ingestion, E7 PowerBI, E13 academic thesis).

---

## `Database_Trades.xlsx` — dimensional source

21 worksheets. The new database collapses the two input/output fact tables into a single `fact_Trades`, drops the KPI sheets (replaced by `v_*` views), and maps each `Dim_*` sheet to a `dim_PascalName` table per the naming convention.

| Excel sheet | Maps to | Notes |
|---|---|---|
| `KPI_Table_Input` | (dropped) | Replaced by `v_employee_performance`, `v_team_performance`, etc. |
| `KPI_Table_Output` | (dropped) | Replaced by the same `v_*` views |
| `Fact_Trades_Input` | `fact_Trades` | Merged with output side, single fact table, partitioned monthly |
| `Fact_Trades_Output` | `fact_Trades` | (same — input/output distinction folded into other columns) |
| `Config_Capital` | `config_Capital` | Baseline 80 000 EUR, effective-date semantics |
| `Dim_Sessions` | `dim_Sessions` | Global FX sessions (London / NY / Asia / Tokyo) |
| `Dim_Sessions_Type` | `dim_SessionType` | |
| `Dim_Accounts` | `dim_Accounts` | DEMO / CHALLENGE / LIVE |
| `Dim_Market` | `dim_Markets` | |
| `Dim_Liquidity` | `dim_Liquidity` | |
| `Dim_Liquidity_Type` | `dim_LiquidityType` | |
| `Dim_Confluences` | `dim_Confluences` | |
| `Dim_Confluences_Type` | `dim_ConfluenceType` | |
| `Dim_Order_Type` | `dim_OrderType` | |
| `Dim_Setup` | `dim_Setup` | |
| `Dim_Re-Entry` | `dim_ReEntry` | Hyphen dropped in target name |
| `Dim_MSS` | `dim_Mss` | "Market Structure Shift" |
| `Dim_Result` | `dim_Result` | Win / Loss / Break Even / Manual Close |
| `Dim_ResultBE` | `dim_ResultBe` | Break-even sub-classification |
| `Dim_News_Impact` | `dim_NewsImpact` | |
| `Dim_News_Type` | `dim_NewsType` | |

Total target tables (excluding `dim_Companies`, `dim_TradingFloors`, `dim_Teams`, `dim_Employees` which are net-new organizational dimensions): **1 fact + 1 config + 16 market dimensions = 18 tables from Excel** + **4 hierarchy dimensions = 22 tables total**, plus 5 views.

Detailed cell-level parsing happens in Etapa 3 (`tcp.excel_ingest`), where each sheet is loaded with `polars.read_excel`, column types are validated, and `sql/002_dimensions.sql` is generated.

---

## `TCP_TradingCentralPanel.pbix` — PowerBI report (visual reference)

Standard .pbix structure (11 entries):

```
Connections                    203 B    DSN binding
DataModel                      ~330 KB  Binary AS tabular model (legacy schema)
DiagramLayout                  ~11 KB   Model diagram positioning
Metadata                       192 B
Report/Layout                  ~3.5 MB  Visual definitions (legacy 5 pages)
Report/StaticResources/.../Custom*.json
Report/StaticResources/.../Logo3_PNG*.png
Report/StaticResources/SharedResources/BaseThemes/CY26SU02.json
Settings                       334 B
Version                        8 B
[Content_Types].xml            591 B
```

The five pages already present in `Report/Layout` are: Calendar, DataBase, Overview, Performance, Edge Analysis. They are **visual references only** for the new build — Etapa 7 reconstructs them programmatically using TMDL (semantic model) and PBIR (page layout) per `docs/decisions/ADR-001-powerbi-deployment.md`.

The embedded `Logo3_PNG*.png` (~52 KB) and the `CY26SU02` theme JSON (~27 KB) may be carried over to the new build for visual continuity — extraction and re-use happens in Etapa 7.

---

## `Ghid_licenta_Informatica_.pdf` — academic documentation requirements

Read tool's PDF parser depends on `pdftoppm` which is not installed in this environment. Detailed parsing is deferred to Etapa 13, where it is the first concrete step:

1. Install `poppler-utils` (or use `pdfplumber` from Python) in the dev environment.
2. Parse the PDF to extract: required chapter structure, page count limits, font/spacing requirements, bibliography style, Turnitin thresholds, bilingual section requirements (RO + EN) for IAG.
3. Document parsed requirements in `docs/decisions/ADR-002-thesis-requirements.md`.
4. Build `thesis/` to spec.

The file is preserved at the repo root and remains untouched.

---

## Next consumption points

- **Etapa 1**: data model design references this inventory (table mapping table)
- **Etapa 2**: SQL DDL uses the target table names from the "Maps to" column above
- **Etapa 3**: `tcp.excel_ingest` reads the actual sheets and produces `sql/002_dimensions.sql`
- **Etapa 7**: PowerBI rebuild references `Report/Layout` for visual continuity and extracts `Logo3_PNG*.png` + `CY26SU02.json` for theming
- **Etapa 13**: PDF parsing produces ADR-002 with thesis requirements
