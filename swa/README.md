# TCP Static Web App frontend (`swa/`)

> **Component scope.** This README documents the `swa/` directory only. For project-wide context, deploy walkthrough, troubleshooting, and the full doc index, see the [top-level README](../README.md). For terminology, see the [glossary](../docs/glossary.md).

Vanilla HTML + JS chat UI for **TCP — Trading Central Panel**. Hosted on
Azure Static Web Apps (Free plan), authenticated via AAD using the SWA
platform's built-in identity provider, and talks to the Function App via the
SWA `linked backend` proxy.

## What this is

A single-page chat client where authenticated TCP employees (traders, team
leads, floor managers) type natural-language questions about trading activity
and receive answers grounded in the Azure SQL data. The page renders:

- A **suggested-questions** panel derived from `docs/design/01_business_requirements.md` §6 use cases (UC-04 / UC-05 / UC-06 / UC-08 / UC-10 / UC-12 / UC-14).
- A **chat transcript** that grows downward, with results rendered both as a natural-language paragraph and (when row count > 0) a small `ro-RO` formatted table.
- A **"Wake up the database"** button that hits `GET /api/ping` to resume the auto-paused SQL serverless instance (per `03_architecture.md` §3.5, the cold-start mitigation control point).

There is **no build step**. The deployable artefact is the contents of this
directory verbatim. No npm, no bundler, no framework.

## Files

| File | Role |
|------|------|
| `index.html` | Page structure, suggested-question list, header, footer, toast container. |
| `style.css` | Design tokens (light + dark via `prefers-color-scheme`), CSS Grid layout, WCAG AA palette. |
| `app.js` | IIFE-wrapped module: `loadCurrentUser`, `wakeDatabase`, `askQuestion`, `renderAnswer`, `renderError`, `bindSuggestedQuestion`. Uses `Intl.NumberFormat('ro-RO')` + `Intl.DateTimeFormat('ro-RO')` for locale formatting. |
| `local.settings.json.example` | Optional SWA-CLI emulator settings (copy to `local.settings.json`, gitignored). |
| `.gitignore` | Local-only IDE / OS artefacts and any future SWA-CLI install state. |

## SWA route configuration

The route table (`/api/ask` → `authenticated`, `/api/ping` → `anonymous`,
catch-all → `anonymous`), forwarding-gateway shared secret, and CSP
`globalHeaders` live in `swa/staticwebapp.config.json`. The SWA deployment
picks that file up because it sits at the root of the directory uploaded
by `azd deploy web`.

### AAD identity provider — built-in, not custom (Etapa-10 arch10-CR-02)

The original config used the **custom** AAD identity provider
(`identityProviders.azureActiveDirectory.registration` with
`openIdIssuer` + `clientIdSettingName`). That path needed two artefacts
that no Bicep module created: (a) a SWA-scoped AAD app registration with
the SWA's reply URL, and (b) a SWA app setting named `AZURE_CLIENT_ID`
holding the app's client id. Etapa 10's cross-cutting architecture review
caught both gaps — the platform would fail at the sign-in handshake with
`auth provider not configured`.

Etapa 10 dropped the custom-provider block in favour of the **built-in**
SWA AAD provider (the `auth` object omits `identityProviders` entirely).
Trade-offs:

- **Tenant pinning lost.** The built-in provider accepts any AAD tenant
  (multi-tenant by default). A real-tenant lockdown is documented as a
  future hardening pass.
- **No manual AAD app registration step** required during `azd up`. The
  academic-phase deploy now runs cleanly without out-of-band Azure-portal
  navigation.
- **`<TENANT_ID>` substitution in `staticwebapp.config.json` becomes a
  no-op** (the placeholder is no longer present). Postprovision Step 2c's
  `<TENANT_ID>` replace is idempotent — leaving it in does no harm.

To re-enable tenant pinning later, restore the `identityProviders` block,
add a `Microsoft.Web/staticSites/config@2023-12-01` child resource in
`infra/modules/swa.bicep` to set `AZURE_CLIENT_ID`, and document the
manual AAD app registration step in `docs/setup.md §B.0`.

The file ships with `<TENANT_ID>` and `<value-set-by-postprovision>`
placeholders that `infra/scripts/postprovision.{sh,ps1}` substitute in
place after `azd provision` resolves the actual AAD tenant id and the
`SWA-FORWARDED-SECRET` Key Vault secret. The placeholder substitution
runs before `azd deploy web`, so the published SWA never sees the
literal placeholder strings.

The file was hosted under `function_app/` in Etapa 4 to be close to the
trigger code; the Etapa-5 holistic review (`review_etapa5_holistic_pass1.md`
MA-06) moved it to `swa/` because `azd deploy web` only uploads the
`swa/` directory — a file under `function_app/` would never reach the
SWA hosting plane.

## Running locally

Prerequisite: the [Azure Static Web Apps CLI](https://azure.github.io/static-web-apps-cli/)
and the [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local).

```bash
# Install the SWA CLI globally (or use npx). One-time setup.
npm install -g @azure/static-web-apps-cli

# From the repo root, start the SWA emulator with the Function App linked as
# the local API. The emulator forwards /api/* to the local Function App,
# mirroring the production linked-backend behaviour.
swa start swa --api-location ./function_app
```

Then open `http://localhost:4280`. The SWA CLI injects a mock
`/.auth/me` principal so the page does not redirect to AAD; the mock identity
can be edited via the emulator UI.

## Deployment

The SWA resource itself is provisioned by `infra/modules/swa.bicep` during
`azd provision`. Content deployment is wired through `azure.yaml`'s `web`
service entry (added during Etapa 5):

```yaml
services:
  web:
    project: ./swa
    language: js   # vanilla JS, no build
    host: staticwebapp
```

After provisioning, push content with:

```bash
azd deploy web
```

`azd` resolves the SWA deployment token from Bicep outputs and uploads the
`swa/` directory to the SWA. No GitHub Actions secret is required at runtime
because the CI pipeline (`cd.yml`) uses the OIDC-federated service principal
to mint a fresh deployment token per run.

## Authentication flow

1. The browser loads `index.html` from the SWA origin (`https://swa-tcp-prod-weu.azurestaticapps.net`).
2. `app.js` calls `GET /.auth/me`. If `clientPrincipal` is null, it redirects to `/.auth/login/aad?post_login_redirect_uri=…`.
3. After AAD sign-in, the SWA platform sets a session cookie and the page reloads with `clientPrincipal` populated. The header shows the user's display name.
4. When the user submits a question, `POST /api/ask` is sent to the SWA origin. The platform forwards it to the Function App and injects the `x-ms-client-principal` header server-side. The browser never carries that header itself.
5. Sign out via the `/.auth/logout` link in the header.

## Backend contract

The chat UI expects every `POST /api/ask` response to carry the unified
JSON envelope below, irrespective of HTTP status code (introduced by the
Etapa-5 holistic review CR-02):

```json
{
  "status":             "ok | refused | validation_error | unauthorized | forbidden | not_found | bad_request | internal_error | rate_limited",
  "answer":             "Natural-language paragraph or null.",
  "rows":               [ { "column_a": ..., "column_b": ... } ] | null,
  "row_count":          42 | null,
  "source":             "v_employee_performance" | null,
  "latency_ms":         834,
  "anthropic":          { "input_tokens": ..., "output_tokens": ..., "cache_read_tokens": ..., "cache_write_tokens": ... } | null,
  "objects_referenced": [ "v_employee_performance", "dim_TradingFloors" ] | null,
  "error":              { "code": "...", "message": "..." } | null
}
```

`app.js#renderAnswer` reads the envelope by `status`:

- `"ok"` → renders the bot bubble with the answer, table, citation, and a
  small token-usage footer that surfaces `anthropic.cache_read_tokens` and
  `objects_referenced` (the cache-discount story the thesis demo relies on).
- `"refused"` / `"validation_error"` → renders the refusal as a first-class
  bot bubble (so the Romanian refusal text persists in the transcript) and
  also surfaces a toast.
- Any other status → maps to the canonical error toast: 401 → "Sign in
  required", 403 → "Forbidden", 404 → "Your account is not registered…",
  500 → "Unexpected error", 429 → "Too many questions, slow down".

## Accessibility

- WCAG 2.1 AA: text contrast ≥ 4.5:1; non-text indicators ≥ 3:1.
- Keyboard-navigable: every interactive element is a real button or link with a visible focus ring (`:focus-visible` outline in `--color-focus`).
- ARIA: `aria-live="polite"` on the transcript log; `aria-label` / labels on every input and button; a skip-link to bypass the header.
- `prefers-reduced-motion` honoured by slowing the spinner.

## Locale

All EUR amounts use `Intl.NumberFormat('ro-RO', { style: 'currency', currency: 'EUR' })`
(yields `12.345,67 €`). All dates use `Intl.DateTimeFormat('ro-RO', { day: '2-digit', month: '2-digit', year: 'numeric' })`
(`dd.MM.yyyy`). UI labels are English by default per the project's
"code/UI strings English" rule.
