-- =====================================================================
-- Naming-convention smoke test (per docs/design/02_database_design.md §14).
--
-- Enforces:
--   - Tables in dbo: ^(fact|dim|config)_[A-Z][a-zA-Z0-9]*$ (exception: schema_history).
--   - Views   in dbo: ^v_[a-z][a-z0-9_]*$
--   - Procs   in dbo: ^usp_[A-Z][a-zA-Z0-9]*$
--   - Scalar functions in dbo: ^fn_[A-Z][a-zA-Z0-9]*$
--   - Inline TVFs in dbo:     ^(fn|tvf)_[A-Z][a-zA-Z0-9]*$
--   - RLS predicate function in rls schema: ^fn_[A-Z][a-zA-Z0-9]*$
--
-- Exits non-zero (via RAISERROR severity 16) on any violation so sqlcmd -b
-- propagates failure to CI.
-- =====================================================================

SET NOCOUNT ON;
GO

DECLARE @violations TABLE (object_type NVARCHAR(20), object_name NVARCHAR(260));

-- Tables under dbo. T-SQL LIKE with a BIN collation gives case-sensitive,
-- pattern-strict matching equivalent to the documented regex
-- ^(fact|dim|config)_[A-Z][a-zA-Z0-9]*$ . The first three NOT LIKE clauses
-- catch a missing/invalid prefix or a non-uppercase first PascalCase letter;
-- the trailing LIKE catches any non-alphanumeric character anywhere in the
-- name (e.g. a stray dash in `dim_FooBar-Baz`).
INSERT INTO @violations
SELECT 'TABLE', t.TABLE_SCHEMA + '.' + t.TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES AS t
WHERE t.TABLE_SCHEMA = 'dbo'
  AND t.TABLE_TYPE   = 'BASE TABLE'
  AND t.TABLE_NAME NOT IN ('schema_history')
  AND (
           (    t.TABLE_NAME COLLATE Latin1_General_BIN NOT LIKE 'fact[_][A-Z]%'
            AND t.TABLE_NAME COLLATE Latin1_General_BIN NOT LIKE 'dim[_][A-Z]%'
            AND t.TABLE_NAME COLLATE Latin1_General_BIN NOT LIKE 'config[_][A-Z]%')
        OR t.TABLE_NAME COLLATE Latin1_General_BIN LIKE '%[^a-zA-Z0-9_]%'
      );

-- Views under dbo: must start with v_ and only contain lower-case letters/digits/underscore after that.
INSERT INTO @violations
SELECT 'VIEW', v.TABLE_SCHEMA + '.' + v.TABLE_NAME
FROM INFORMATION_SCHEMA.VIEWS AS v
WHERE v.TABLE_SCHEMA = 'dbo'
  AND (
        v.TABLE_NAME COLLATE Latin1_General_BIN NOT LIKE 'v[_][a-z]%'
     OR v.TABLE_NAME COLLATE Latin1_General_BIN LIKE '%[^a-z0-9_]%'
  );

-- Stored procedures under dbo.
INSERT INTO @violations
SELECT 'PROC', SCHEMA_NAME(p.schema_id) + '.' + p.[name]
FROM sys.procedures AS p
WHERE SCHEMA_NAME(p.schema_id) = 'dbo'
  AND p.is_ms_shipped = 0
  AND p.[name] COLLATE Latin1_General_BIN NOT LIKE 'usp[_][A-Z]%';

-- Functions under dbo: fn_PascalCase (scalar/T-SQL) or tvf_PascalCase (inline TVF).
INSERT INTO @violations
SELECT 'FUNCTION', SCHEMA_NAME(o.schema_id) + '.' + o.[name]
FROM sys.objects AS o
WHERE SCHEMA_NAME(o.schema_id) = 'dbo'
  AND o.is_ms_shipped = 0
  AND o.[type] IN ('FN', 'IF', 'TF')
  AND o.[name] COLLATE Latin1_General_BIN NOT LIKE 'fn[_][A-Z]%'
  AND o.[name] COLLATE Latin1_General_BIN NOT LIKE 'tvf[_][A-Z]%';

-- Functions under rls schema: predicate function must be fn_PascalCase.
INSERT INTO @violations
SELECT 'RLS_FUNCTION', SCHEMA_NAME(o.schema_id) + '.' + o.[name]
FROM sys.objects AS o
WHERE SCHEMA_NAME(o.schema_id) = 'rls'
  AND o.is_ms_shipped = 0
  AND o.[type] IN ('FN', 'IF', 'TF')
  AND o.[name] COLLATE Latin1_General_BIN NOT LIKE 'fn[_][A-Z]%';

IF EXISTS (SELECT 1 FROM @violations)
BEGIN
    SELECT object_type, object_name FROM @violations ORDER BY object_type, object_name;
    RAISERROR('Naming-convention violations found.', 16, 1);
END
ELSE
    PRINT 'Naming convention: OK';
GO
