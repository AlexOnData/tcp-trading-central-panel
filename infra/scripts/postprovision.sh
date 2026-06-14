#!/bin/bash

################################################################################
# Post-provision script for TCP — Trading Central Panel
#
# Runs after 'azd provision' to finalize RLS setup, AAD-only auth flip,
# and secret management.
#
# Invoked by: azd hooks (infra/postprovision in azure.yaml)
# Idempotent: safe to run multiple times
################################################################################

set -euo pipefail

# Logging helpers
info() {
  echo "[INFO] $*" >&2
}

success() {
  echo "[SUCCESS] $*" >&2
}

warn() {
  echo "[WARNING] $*" >&2
}

error() {
  echo "[ERROR] $*" >&2
  exit 1
}

# Parse azd environment
info "Reading azd environment..."

SQL_SERVER_NAME=$(azd env get-value AZURE_SQL_SERVER_NAME)
SQL_DATABASE_NAME=$(azd env get-value AZURE_SQL_DATABASE_NAME)
RESOURCE_GROUP=$(azd env get-value AZURE_RESOURCE_GROUP)
FUNCTION_APP_NAME=$(azd env get-value AZURE_FUNCTION_APP_NAME)
KV_NAME=$(azd env get-value AZURE_KEYVAULT_NAME)
FUNCTION_APP_PRINCIPAL_ID=$(azd env get-value AZURE_FUNCTION_APP_PRINCIPAL_ID)
AZURE_PRINCIPAL_ID=$(azd env get-value AZURE_PRINCIPAL_ID)

[[ -n "$SQL_SERVER_NAME" ]] || error "AZURE_SQL_SERVER_NAME not found"
[[ -n "$SQL_DATABASE_NAME" ]] || error "AZURE_SQL_DATABASE_NAME not found"
[[ -n "$RESOURCE_GROUP" ]] || error "AZURE_RESOURCE_GROUP not found"
[[ -n "$AZURE_PRINCIPAL_ID" ]] || error "AZURE_PRINCIPAL_ID not found in azd env. Run 'azd env set AZURE_PRINCIPAL_ID <object-id>' before postprovision."

SQL_SERVER_FQDN="${SQL_SERVER_NAME}.database.windows.net"

info "Parsed configuration:"
info "  SQL Server: $SQL_SERVER_NAME"
info "  SQL Database: $SQL_DATABASE_NAME"
info "  Resource Group: $RESOURCE_GROUP"
info "  Function App: $FUNCTION_APP_NAME"
info "  Function App MI OID: $FUNCTION_APP_PRINCIPAL_ID"
info "  AAD admin candidate OID: $AZURE_PRINCIPAL_ID"
info "  Key Vault: $KV_NAME"

# Helper: execute SQL against the target database
execute_sql() {
  local sql_script="$1"
  info "Executing SQL..."
  echo "$sql_script" | sqlcmd -S "$SQL_SERVER_FQDN" -d "$SQL_DATABASE_NAME" -G -b 2>&1
}

# Step 0a: Register the deploying principal as AAD admin on the SQL server.
# Required because sql.bicep deliberately omits the `administrators` block
# (preferring imperative registration here so the admin identity differs
# across CI vs interactive deploy). Without this step, the `sqlcmd -G` calls
# below fail with `Login failed for token-identified principal`.
# Idempotent: a second call against an existing AAD admin is a no-op.
info "Step 0a: Registering AAD admin on SQL Server..."

az sql server ad-admin create \
  --resource-group "$RESOURCE_GROUP" \
  --server-name "$SQL_SERVER_NAME" \
  --display-name "tcp-deployer" \
  --object-id "$AZURE_PRINCIPAL_ID" \
  --output none

# Brief settle delay so the next sqlcmd sees the admin assignment.
sleep 5
success "AAD admin registered for object id $AZURE_PRINCIPAL_ID."

# Step 0: Apply schema migrations (V001, V002) — idempotent, safe to re-run.
# Must precede Step 1 because the RLS policy + dim_UserRoles table do not exist
# until V001 has applied.
#
# Each file's `__V<n>_CHECKSUM__` placeholder is replaced with the SHA-256
# computed by `scripts/compute_migration_checksum.py` BEFORE piping to sqlcmd
# (RR-09 from docs/security/threat_model.md). The checksum is computed against
# the on-disk file (placeholder included), so the value is stable across
# re-applies — only a substantive edit changes it.
info "Step 0: Applying schema migrations..."

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Capture all checksums in one shot so the loop is deterministic and the call
# to the python helper happens just once per deploy.
declare -A CHECKSUMS
while IFS='=' read -r key value; do
  CHECKSUMS["$key"]="$value"
done < <(python3 "$REPO_ROOT/scripts/compute_migration_checksum.py")

if [[ ${#CHECKSUMS[@]} -eq 0 ]]; then
  error "compute_migration_checksum.py returned no checksums."
fi

for migration in "$REPO_ROOT/db/migrations/V001__init.sql" "$REPO_ROOT/db/migrations/V002__synth_logic.sql"; do
  if [[ ! -f "$migration" ]]; then
    error "Migration file not found: $migration"
  fi
  base="$(basename "$migration")"
  # Derive the same variable name shape as the helper script: V001 from
  # V001__init.sql, V002 from V002__synth_logic.sql.
  prefix="${base%%__*}"
  var="${prefix}_CHECKSUM"
  checksum="${CHECKSUMS[$var]:-}"
  if [[ -z "$checksum" ]]; then
    error "Checksum for $base not produced by helper (looked up key $var)."
  fi
  placeholder="__${prefix}_CHECKSUM__"
  info "  Applying $base (checksum ${checksum:0:8}…)..."
  # arch-MA-04: shared Python helper so the rendered bytes match the
  # PowerShell path exactly. code-MA-04: assign the rendered value
  # separately and check $? so a python crash does not silently produce
  # an empty stream that `sqlcmd -b` would treat as success.
  if ! rendered=$(python3 "$REPO_ROOT/scripts/render_migration.py" \
      --path "$migration" \
      --placeholder "$placeholder" \
      --checksum "$checksum"); then
    error "render_migration.py failed for $base."
  fi
  if [[ -z "$rendered" ]]; then
    error "render_migration.py returned an empty stream for $base."
  fi
  printf '%s' "$rendered" | sqlcmd -S "$SQL_SERVER_FQDN" -d "$SQL_DATABASE_NAME" -G -b
done
success "Schema migrations applied (checksums recorded in dbo.schema_history)."

# Step 1: Register Function App MI in RLS table
info "Step 1: Registering Function App MI in RLS table..."

# Use direct variable expansion (not 'EOF') to safely substitute the GUID
SQL_SETUP=$(cat <<EOF
BEGIN TRY
  ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = OFF);

  IF NOT EXISTS (
    SELECT 1 FROM dbo.dim_UserRoles
    WHERE aad_object_id = CAST('$FUNCTION_APP_PRINCIPAL_ID' AS UNIQUEIDENTIFIER)
      AND scope = 'admin'
      AND is_active = 1
  )
  BEGIN
    INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active, created_at)
    VALUES (CAST('$FUNCTION_APP_PRINCIPAL_ID' AS UNIQUEIDENTIFIER), NULL, 'admin', 1, SYSDATETIMEOFFSET());
    PRINT 'Registered Function App MI as admin.';
  END
  ELSE
  BEGIN
    PRINT 'Function App MI already registered.';
  END;
END TRY
BEGIN CATCH
  ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON);
  THROW;
END CATCH;

ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON);
EOF
)

execute_sql "$SQL_SETUP" || {
  warn "SQL RLS setup failed; Step 0 should have applied V001 already. Investigate."
  exit 1
}

# Trap to ensure RLS is always re-enabled on script exit
trap 'sqlcmd -S "$SQL_SERVER_FQDN" -d "$SQL_DATABASE_NAME" -G -b 2>&1 <<EOF
ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON);
EOF
' EXIT

success "RLS setup complete."

# Step 2: Set TCP_GENERATOR_OID app setting
info "Step 2: Setting TCP_GENERATOR_OID app setting..."

az functionapp config appsettings set \
  --name "$FUNCTION_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --settings "TCP_GENERATOR_OID=$FUNCTION_APP_PRINCIPAL_ID" \
  --output none

success "TCP_GENERATOR_OID set to $FUNCTION_APP_PRINCIPAL_ID"

# Step 2b: Restart the Function App to pick up the new app setting
info "Step 2b: Restarting Function App to load new settings..."

az functionapp restart \
  --name "$FUNCTION_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --output none

success "Function App restarted."

# Step 2c: Substitute placeholders in swa/staticwebapp.config.json
# (per Etapa-4 convergence pass-2 security CR-03; moved from function_app/ to
# swa/ in the Etapa-5 review pass — the SWA build pipeline uploads `swa/`
# verbatim and would never see a file living next to the Function App).
info "Step 2c: Substituting SWA config placeholders..."

SWA_CONFIG_PATH="$REPO_ROOT/swa/staticwebapp.config.json"
if [[ ! -f "$SWA_CONFIG_PATH" ]]; then
  echo "staticwebapp.config.json not found at $SWA_CONFIG_PATH" >&2
  exit 1
fi
if [[ -z "${AZURE_TENANT_ID:-}" ]]; then
  echo "AZURE_TENANT_ID not in env; cannot substitute placeholder." >&2
  exit 1
fi
SWA_SECRET=$(az keyvault secret show --vault-name "$KV_NAME" --name 'SWA-FORWARDED-SECRET' --query value -o tsv)
if [[ -z "$SWA_SECRET" ]]; then
  echo "SWA-FORWARDED-SECRET not found in $KV_NAME." >&2
  exit 1
fi
# Use python for safe replacement (preserves JSON escaping; avoids sed delimiter issues).
python3 - "$SWA_CONFIG_PATH" "$AZURE_TENANT_ID" "$SWA_SECRET" <<'PYEOF'
import sys
path, tenant, secret = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as f:
    content = f.read()
content = content.replace("<TENANT_ID>", tenant).replace("<value-set-by-postprovision>", secret)
with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(content)
PYEOF
success "staticwebapp.config.json placeholders substituted."

# Step 3: Enable AAD-only authentication on SQL server
info "Step 3: Enabling AAD-only authentication on SQL server..."

az sql server ad-only-auth enable \
  --resource-group "$RESOURCE_GROUP" \
  --server-name "$SQL_SERVER_NAME"

sleep 10  # Allow the change to propagate
success "AAD-only authentication enabled."

# Step 4: Delete SQL-ADMIN-PASSWORD-BOOTSTRAP
info "Step 4: Cleaning up bootstrap password..."

if az keyvault secret show \
  --vault-name "$KV_NAME" \
  --name 'SQL-ADMIN-PASSWORD-BOOTSTRAP' \
  --output none 2>/dev/null; then
  az keyvault secret delete \
    --vault-name "$KV_NAME" \
    --name 'SQL-ADMIN-PASSWORD-BOOTSTRAP'
  success "Deleted SQL-ADMIN-PASSWORD-BOOTSTRAP."
else
  info "SQL-ADMIN-PASSWORD-BOOTSTRAP not found (already deleted)."
fi

# Step 5: Verify AAD-only flip
info "Step 5: Verifying AAD-only authentication..."

AAD_STATUS=$(az sql server ad-only-auth get \
  --resource-group "$RESOURCE_GROUP" \
  --server-name "$SQL_SERVER_NAME" \
  --query 'azureADOnlyAuthentication' \
  --output tsv)

if [[ "$AAD_STATUS" == "True" ]]; then
  success "AAD-only authentication verified as enabled."
else
  error "AAD-only authentication is not enabled!"
fi

# Verify bootstrap secret is gone
if ! az keyvault secret show \
  --vault-name "$KV_NAME" \
  --name 'SQL-ADMIN-PASSWORD-BOOTSTRAP' \
  --output none 2>/dev/null; then
  success "SQL-ADMIN-PASSWORD-BOOTSTRAP confirmed deleted."
else
  error "SQL-ADMIN-PASSWORD-BOOTSTRAP still exists in Key Vault!"
fi

# Final summary
success "Post-provision complete!"
info "Summary:"
info "  ✓ Function App MI registered in RLS table as admin"
info "  ✓ TCP_GENERATOR_OID app setting configured"
info "  ✓ AAD-only authentication enabled on SQL server"
info "  ✓ SQL-ADMIN-PASSWORD-BOOTSTRAP removed from Key Vault"
info "  ✓ SQL-ADMIN-PASSWORD-EXPORT retained for BACPAC exports"
