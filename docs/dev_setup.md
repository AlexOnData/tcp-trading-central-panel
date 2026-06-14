# Development Setup — TCP — Trading Central Panel

This guide walks you through setting up a local development environment for TCP and running the full test suite.

---

## Prerequisites

### Required

- **Python 3.12** — Download from [python.org](https://www.python.org/downloads/) or use your OS package manager.
- **uv** — Package manager for Python. Install via `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows).
- **Git** — For cloning the repository.

### Optional (for local SQL Server development)

- **Docker and Docker Compose** — Required to spin up a local SQL Server 2022 container.
  - macOS: [Docker Desktop](https://www.docker.com/products/docker-desktop)
  - Windows: [Docker Desktop](https://www.docker.com/products/docker-desktop) or WSL2
  - Linux: `apt install docker-ce docker-compose` (Debian/Ubuntu) or equivalent for your distro.
- **sqlcmd** — SQL Server command-line tool.
  - macOS: `brew install sqlcmd`
  - Windows: Included in [Azure Data Studio](https://learn.microsoft.com/en-us/sql/azure-data-studio/download-azure-data-studio) or install via `choco install mssql-tools18` (Chocolatey).
  - Linux: `apt install mssql-tools18` (Debian/Ubuntu).

---

## One-Time Setup

> **Local-only password — DO NOT use in any shared environment.** The placeholder
> `YourStrong!Passw0rd` is the documented default for the local Docker SA account
> and is allowlisted in `.gitleaks.toml`. Before exposing the container to any
> non-loopback interface, change the password by exporting `TCP_SQL_DEV_PASSWORD`
> to a unique strong secret.

### 1. Clone and enter the repository

```bash
# Linux/macOS (bash)
git clone https://github.com/TODO/tcp-trading-central-panel.git
cd tcp-trading-central-panel
```

```powershell
# Windows (PowerShell)
git clone https://github.com/TODO/tcp-trading-central-panel.git
Set-Location tcp-trading-central-panel
```

### 2. Install Python dependencies via uv

```bash
# Linux/macOS (bash) — and Windows (PowerShell), identical invocation
uv sync --all-extras
```

This creates a virtual environment (`.venv`) and installs all dependencies, including dev tools (`pytest`, `ruff`, `mypy`).

---

## Local SQL Server Setup

### 1. Start the Docker container

Export the SQL Server admin password and start the container:

```bash
# Linux/macOS (bash)
export TCP_SQL_DEV_PASSWORD='YourStrong!Passw0rd'
docker compose -f docker-compose.dev.yml up -d
```

```powershell
# Windows (PowerShell)
$env:TCP_SQL_DEV_PASSWORD = 'YourStrong!Passw0rd'
docker compose -f docker-compose.dev.yml up -d
```

Verify the container is healthy:

```bash
docker ps
```

The healthcheck will retry for up to 2.5 minutes before giving up. Wait for the status to show `healthy`.

### 2. Create the development database

```bash
# Linux/macOS (bash)
docker exec tcp-sql-dev /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$TCP_SQL_DEV_PASSWORD" -C -Q "CREATE DATABASE tcp_dev"
```

```powershell
# Windows (PowerShell)
docker exec tcp-sql-dev /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$env:TCP_SQL_DEV_PASSWORD" -C -Q "CREATE DATABASE tcp_dev"
```

### 3. Apply the schema migration

Run the initial migration (`V001__init.sql`) to create all tables, views, and RLS policies:

```bash
# Linux/macOS (bash)
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev -i db/migrations/V001__init.sql -b -C
```

```powershell
# Windows (PowerShell)
sqlcmd -S localhost,1433 -U sa -P "$env:TCP_SQL_DEV_PASSWORD" -d tcp_dev -i db/migrations/V001__init.sql -b -C
```

**Flags:**
- `-S` — server and port
- `-U` — username
- `-P` — password
- `-d` — database name
- `-i` — input SQL file
- `-b` — on error, exit (fail-fast)
- `-C` — trust server certificate (localhost, self-signed)

---

## Running Tests

### Unit tests (no database required)

```bash
# Linux/macOS (bash) — and Windows (PowerShell), identical invocation
uv run pytest tests/unit -v
```

This runs fast tests that do not touch SQL, ideal for rapid feedback during development.

### Integration tests (requires local SQL)

First, ensure the local database is up and migrated (see SQL Server Setup above). Then:

```bash
# Linux/macOS (bash)
export TCP_SQL_SERVER='localhost,1433'
export TCP_SQL_DATABASE='tcp_dev'
export TCP_SQL_DEV_USER='sa'
export TCP_SQL_DEV_PASSWORD='YourStrong!Passw0rd'
uv run pytest tests/integration -v -m integration
```

```powershell
# Windows (PowerShell)
$env:TCP_SQL_SERVER   = 'localhost,1433'
$env:TCP_SQL_DATABASE = 'tcp_dev'
$env:TCP_SQL_DEV_USER = 'sa'
$env:TCP_SQL_DEV_PASSWORD = 'YourStrong!Passw0rd'
uv run pytest tests/integration -v -m integration
```

### SQL integration tests

These verify schema correctness, naming conventions, and RLS contracts:

```bash
# Linux/macOS (bash)
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev -i tests/sql/test_naming_convention.sql -b -C
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev -i tests/sql/test_rls_smoke.sql -b -C
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev -i tests/sql/test_fx_rate_completeness.sql -b -C
```

```powershell
# Windows (PowerShell)
sqlcmd -S localhost,1433 -U sa -P "$env:TCP_SQL_DEV_PASSWORD" -d tcp_dev -i tests/sql/test_naming_convention.sql -b -C
sqlcmd -S localhost,1433 -U sa -P "$env:TCP_SQL_DEV_PASSWORD" -d tcp_dev -i tests/sql/test_rls_smoke.sql -b -C
sqlcmd -S localhost,1433 -U sa -P "$env:TCP_SQL_DEV_PASSWORD" -d tcp_dev -i tests/sql/test_fx_rate_completeness.sql -b -C
```

Each script prints `PASS` or `FAIL` and returns a non-zero exit code on assertion failure.

---

## Code Style and Linting

### Format code

```bash
uv run ruff format .
```

### Check for violations

```bash
uv run ruff check .
uv run mypy tcp tests
```

**Notes:**
- **ruff**: Enforces PEP 8 and imports; see `pyproject.toml [tool.ruff]`.
- **mypy**: Type checker with `strict = true`; docstrings are required on public functions (Google style).
- All rules must pass before merging to `main`.

---

## Cleaning Up

### Stop and remove the local SQL Server container

```bash
docker compose -f docker-compose.dev.yml down -v
```

The `-v` flag removes the named volume `tcp-sql-data`, destroying all local data. Omit `-v` if you want to preserve the volume for later reuse.

---

## Working Against Azure SQL (Post-Etapa 4)

Once the Bicep infrastructure is deployed, you can test against the cloud database without Docker:

```bash
az login   # or az login --use-device-code
sqlcmd -S sql-tcp-prod-weu.database.windows.net -d sqldb-tcp-prod-weu -G -i db/migrations/V001__init.sql -b
```

**Flags:**
- `-G` — use Azure AD (Managed Identity or user credential) instead of SQL auth.
- Requires `sqlcmd` >= 21.0 (support for `-G`).

---

## Troubleshooting

### Docker: "ODBC Driver 18 not found"

The `sqlcmd` container tool requires the Microsoft ODBC Driver for SQL Server. For local Linux systems:

```bash
# Debian/Ubuntu
curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list | tee /etc/apt/sources.list.d/mssql-release.list
apt-get update
apt-get install -y msodbcsql18

# RHEL/CentOS
yum install -y mssql-tools18
```

### sqlcmd hangs or times out

1. Check that the Docker container is running: `docker ps | grep tcp-sql-dev`.
2. Verify the healthcheck passed: `docker ps` should show status `healthy` (not `starting` or `unhealthy`).
3. Check firewall rules on port 1433.
4. Try a simple health check: `docker exec tcp-sql-dev /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$TCP_SQL_DEV_PASSWORD" -C -Q "SELECT 1"`.

### pytest import errors

Ensure you are running pytest through `uv run`:

```bash
uv run pytest tests/unit -v   # correct
pytest tests/unit -v           # may fail if venv is not active
```

### ruff docstring violations (D rule failures)

TCP follows Google-style docstrings. Every public function (not prefixed with `_`) must have a 1–3 line docstring explaining what the function does:

```python
def fetch_employee(emp_id: int) -> dict[str, Any]:
    """Fetch an employee record by ID from the database."""
    ...
```

See CLAUDE.md § Python conventions and the `pyproject.toml [tool.ruff.lint.pydocstyle]` section for details.

---

## References

- **Database design**: `docs/design/02_database_design.md`
- **RLS contract**: `docs/decisions/ADR-003-rls-session-context.md`
- **Python code structure**: `tcp/README.md`
- **Database migrations**: `db/migrations/` and `db/README.md` (if present)
- **Integration guide**: Companion to `docs/design/03_architecture.md` §6 and §9.
