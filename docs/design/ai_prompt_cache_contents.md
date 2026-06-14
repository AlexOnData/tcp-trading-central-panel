# AI prompt-cache contents

> **Document scope.** Enumerates exactly what lives inside the
> Anthropic prompt-cache block sent on every `POST /api/ask` call,
> explains the token-budget math, and pins the trade-offs against
> `docs/design/03_architecture.md §3.2`,
> `docs/decisions/ADR-003-rls-session-context.md`, and the Etapa-1
> holistic review story-completeness gap (MA-07).
> **Stage:** Etapa 5 — AI assistant body.
> **Source artifacts:** `tcp/ai/prompts.py::SCHEMA_SYSTEM_PROMPT`,
> `tcp/ai/anthropic_client.py::ask_claude`,
> `function_app/triggers/ask.py`.

---

## 1. Why a single cached system block?

Anthropic charges full input-token rates for the system prompt on the
first call, then a 90 % discount on every subsequent call whose system
content hashes to the same value, for the duration of the cache TTL
(currently 5 minutes for the `ephemeral` cache control). The `/api/ask`
trigger sends a ~3 500-token schema preamble on every call. Without
caching that is `~3 500 × N` input tokens per minute; with caching it
is `3 500 + 350 × (N - 1)` ≈ a 9× input-cost reduction at steady state.

The cached block is **strictly read-only and PII-free**: it carries view
DDL, proc signatures, function signatures, the locale rules, and three
few-shot examples. **No row data lives in this block.** Per-request
context (the user's question and their resolved RLS scope) is sent as
the regular `user` message and is **not** cached.

## 2. Contents of `SCHEMA_SYSTEM_PROMPT`

The full prompt body lives at `tcp/ai/prompts.py`. Below is the
section-by-section map.

### Section 1 — Role definition (≈ 80 tokens)

A short paragraph introducing the model as the **TCP analytics
assistant** for **TCP Capital Management SRL**, with the 2-floor /
6-team / 32-employee context. Pins the two-piece output expectation:
one SELECT + one answer template.

### Section 2 — Allowlisted objects (≈ 350 tokens)

Three sub-lists:

- **Views** — the five `v_*` views (`v_trades_enriched`,
  `v_employee_performance`, `v_team_performance`,
  `v_floor_performance`, `v_daily_pnl`). Cross-references
  `02_database_design.md §6`.
- **Dimensions** — the nine `dim_*` lookups
  (`dim_Companies` … `dim_Date`). Explicit note that
  **`dim_UserRoles` is excluded** so the model never references the
  RLS scope map.
- **Stored procedures** — `usp_GetEmployeePerformance` and
  `usp_GetTopPerformers` only. `usp_GenerateDailyTrades` is intentionally
  absent.
- **Functions / TVFs** — `fn_GetCapitalBaseline`, `tvf_GetCapitalBaseline`,
  `tvf_RiskMetrics`, `fn_IsTradingDay`, `fn_PreviousBusinessDay`.

The model is told that the host's SQL validator rejects anything outside
this allowlist, so emitting an unrecognised name guarantees a 422 from
`tcp.safe_query.validate`.

### Section 3 — View definitions (≈ 1 800 tokens)

Verbatim column dictionaries for the five reporting views, copy-pasted
from `02_database_design.md §6`. For each view we include:

- The grain (e.g., "one row per `(trade_date_ro, employee_id)`").
- The full column list with data types and short descriptions.
- Where relevant, the `SCHEMABINDING` note that constrains downstream
  queries (`v_trades_enriched` is a base view, the others derive from it).

This is the bulk of the cached payload and the reason caching matters —
serving these columns on every call without caching would dominate the
input-token budget.

### Section 4 — RLS contract (≈ 220 tokens)

Plain-language explanation that:

- `SESSION_CONTEXT('aad_object_id')` is set by the host before the
  query runs (ADR-003 §2).
- The RLS policy filters `fact_Trades` by the caller's scope (trader /
  team_lead / floor_manager / admin).
- The model **must not** reference `SESSION_CONTEXT`,
  `sp_set_session_context`, `dim_UserRoles`, or `fact_Trades` directly.

This section is what closes Etapa 1's holistic-review gap (MA-07): the
LLM previously had no documented awareness of the RLS contract.

### Section 5 — Hard rules for emitted SQL (≈ 160 tokens)

- Single SELECT statement only.
- `TOP n` with `n <= 1000` (the validator clamps anyway).
- Default date scope is Europe/Bucharest via `trade_date_ro`.
- Holiday handling is implicit (`v_employee_performance` only has rows
  on trading days).

### Section 6 — Locale rules (≈ 90 tokens)

The Romanian-locale conventions from `CLAUDE.md` and `02_DB §0`:

- Decimal: `,` thousands: `.` — example: `12.345,67 €`.
- Dates: `dd.MM.yyyy`.
- Percentages: `54,3 %`.
- Two-decimal rounding for PnL.

### Section 7 — Tool contract (≈ 200 tokens)

The exact JSON shape the model must emit through the `emit_sql` tool —
five fields: `sql`, `answer_template`, `citation`, `refused`,
`refusal_reason`. The template placeholder set (`{row_count}`,
`{value:<col>}`, `{rows}`) is enumerated here.

### Section 8 — Refusal policy (≈ 150 tokens)

The model is told to refuse questions about other customers, personal
data, out-of-scope queries, mutating commands, or scope-bypass attempts.
Refusals fill `refusal_reason` in the user's language and leave `sql`
empty.

### Section 9 — Few-shot examples (≈ 450 tokens)

Three worked examples covering:

1. A trader-scope daily-PnL question in Romanian.
2. A floor-manager-scope top-N performers question in Romanian.
3. An out-of-scope refusal in Romanian.

Each example shows the exact JSON `emit_sql` payload — including the
Europe/Bucharest timezone cast — so the model has concrete patterns to
copy.

## 3. Estimated token count

Adding the sections together: **3 500 ± 200 tokens** by the 4-chars-per-
token heuristic. The actual count is on Anthropic's side and we record
it via `tcp.ask.cache_read_tokens` / `tcp.ask.cache_write_tokens`
custom metrics.

| Section | Approx tokens |
|---|---|
| Role definition | 80 |
| Allowlisted objects | 350 |
| View definitions | 1 800 |
| RLS contract | 220 |
| Hard SQL rules | 160 |
| Locale rules | 90 |
| Tool contract | 200 |
| Refusal policy | 150 |
| Few-shot examples | 450 |
| **Total** | **≈ 3 500** |

The per-request input budget excluding cached tokens is capped at
`AnthropicConfig.max_input_tokens = 2 000` in
`tcp/ai/anthropic_client.py`; the wrapped user message is much smaller
than this in normal use.

## 4. Cache TTL

The Anthropic `cache_control: {"type": "ephemeral"}` setting yields a
**5-minute TTL** at the time of writing. A demo session (≤ 30 questions)
keeps the cache warm across the whole conversation; a 6-minute idle
period flushes the cache and the next call pays the full 3 500-token
write before resuming the 90 % discount on the call after that.

If we ever need a longer TTL (e.g., for multi-day batch evaluation),
Anthropic may offer extended TTLs (1 h / 24 h) on a contractual basis —
confirm with the API team before relying on it. The public SDK exposes
the `ephemeral` cache control only at the time of writing; longer TTLs
are out of scope for the academic build.

## 5. What is NOT in the cache

- The user's question (per-request `messages[0].content`).
- The user's resolved scope (passed through `build_user_message`).
- Any row data — `tcp/safe_query.py` is the security boundary and the
  prompt never sees rows.
- The `ANTHROPIC_API_KEY` (held in `SecretStr`, redacted in every log).

This separation is intentional: a cache content hash that mixed per-
request data would defeat the cache entirely (every request would
produce a unique hash and a new cache write).

## 6. Cross-references

- `docs/design/03_architecture.md §3.2` (user-question path) — the
  overall flow that wraps this cached block.
- `docs/design/03_architecture.md §6.4` (`safe_query.py` contract) —
  the validator the model's SQL passes through after this prompt-call
  step.
- `docs/decisions/ADR-003-rls-session-context.md` — the RLS contract
  referenced in section 4.
- `docs/design/reviews/review_holistic_pass1.md` MA-07 — the originating
  finding that drove section 4 into the prompt body.

## 7. AI assistant scope vs PowerBI scope

> **Closes arch10-MJ-05** (Etapa-10 architecture review): the
> `01_business_requirements.md` KPI catalogue lists 48 KPIs, but the
> `safe_query` allowlist exposes a narrower view surface. This section
> makes the gap explicit so a defender of the thesis (or an operator
> answering a stakeholder request) can route the question to the right
> channel without guessing.

The AI assistant and the PowerBI dashboard share the same database, but
each surface is optimised for a different kind of question. The
allowlist in `tcp/safe_query.py` deliberately exposes a subset of the
schema — only the `v_*` views, the `dim_*` dimensions, the two read-only
procs, and the five risk/percentile functions. PowerBI's TMDL semantic
model sits on the same `v_*` views but adds 69 DAX measures that
implement multi-period calculations, ratios, and ranking that are hard
to express as a single allowlisted SELECT.

| KPI family (from [`01_BR §4`](01_business_requirements.md)) | AI assistant — direct answer | PowerBI — primary surface | Notes |
|---|---|---|---|
| **Volume** (trade count, lot size, asset class mix) | Yes | Yes | Native `v_trades_enriched` aggregation. |
| **PnL — single period** (gross / net per trader, team, floor, company) | Yes | Yes | `v_employee_performance`, `v_team_performance`, `v_floor_performance`, `v_daily_pnl`. |
| **PnL — multi-period growth** (MoM, QoQ, YoY) | No | Yes | DAX time-intelligence measures (`SAMEPERIODLASTYEAR`, calculation groups) are not expressible as a single allowlisted SELECT without window-on-window joins. Surface in PowerBI. |
| **Performance-vs-Capital** (ROC, Sharpe, Sortino) | Yes | Yes | The `tvf_RiskMetrics` function is in the allowlist; the AI assistant can call it directly with `(trader_id, from_date, to_date)` arguments. |
| **Risk** (Max DD, Profit Factor, VaR, Average Win / Loss) | Yes (single trader) | Yes (any granularity) | `tvf_RiskMetrics` handles the per-trader case; cross-trader aggregations (e.g., team-wide Max DD) need PowerBI's row-context measures. |
| **Behavioral** (holding time, intraday split, weekend carry) | Partial | Yes | The intraday / weekend / consecutive-loss-streak DAX approximations are documented in [`powerbi/README.md`](../../powerbi/README.md) "Known limitations"; the AI assistant computes them exactly via ordered CTEs. |
| **Quality** (win rate, profitable-day rate) | Yes | Yes | Direct `v_*` aggregation. |
| **Team / Floor aggregates** | Yes | Yes | `v_team_performance`, `v_floor_performance`. |
| **Leadership multiplier** | No | Yes | The leadership-multiplier model is a PowerBI-only DAX construct (it cross-references team and floor leads against their subordinates' aggregated KPIs); not in the AI allowlist by design. |
| **Cross-period drawdown analytics** (rolling 30/90-day DD, drawdown vs benchmark) | No | Yes | Requires anchored window functions over arbitrary periods; PowerBI's calculation-group `DATESINPERIOD` is the natural fit. |

**Routing heuristic**: if the question fits in a single SELECT against
the `v_*` views with at most one TVF call, the AI assistant is the
right surface; if the question requires *multi-period comparison*,
*ranking across rolling windows*, or *aggregated drawdown analytics*,
the PowerBI dashboard is the right surface. The **PowerBI AI Assistant
page** carries a hyperlink/button visual linking out to the SWA chat UI
(per Etapa-7 hardening — the Etapa-6 `X-Frame-Options: DENY` + CSP
`frame-ancestors 'none'` rules block any iframe embed, so a hyperlink
is the only viable in-report pivot). The user can therefore navigate
from the BI dashboard to the natural-language assistant without
leaving the session.

This split is intentional and is **not** a defect: it is the same
tier-1 / tier-2 contract that production analytics platforms typically
draw between an ad-hoc query interface and a BI semantic layer. The AI
assistant prioritises *correctness under RLS* and *latency*; PowerBI
prioritises *expressive power* over a fixed-schema model. Together
they cover all 48 KPI families from `01_BR §4`.

## 8. Maintenance notes

- **Editing the prompt invalidates the cache once.** The next request
  after a deploy pays a full 3 500-token write; the request after that
  resumes the 90 % discount.
- **Token-count regression guard.** A unit test in
  `tests/unit/test_ai_anthropic_client.py` asserts the system block
  carries `cache_control: ephemeral` and that the model id / temperature
  pin the production contract; a future test should additionally
  assert `len(SCHEMA_SYSTEM_PROMPT) < 20 000` (≈ 5 000 tokens) so a
  doc rewrite cannot inflate the budget unnoticed.
- **PII firewall.** Never paste row data, employee names, or AAD object
  ids into `SCHEMA_SYSTEM_PROMPT`. The body should describe shape, not
  content.
- **Schema/precision parity.** The cached prompt declares
  `gross_pnl_eur`, `commission_eur`, `net_pnl_eur` as
  `DECIMAL(18,4)` to match `02_database_design.md §6.1` exactly
  (Etapa-5 holistic review MA-01). A future schema change that bumps
  the precision must update this file in the same commit to avoid the
  one-call cache-miss penalty surfacing twice.
