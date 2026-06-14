-- Create contained DB user for the PowerBI Service Principal and grant the
-- `tcp_bi_reader` role (PowerBI dataset import path). Idempotent.

SET QUOTED_IDENTIFIER ON;

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'tcp-powerbi-sp')
BEGIN
    CREATE USER [tcp-powerbi-sp] FROM EXTERNAL PROVIDER;
    PRINT 'Created user tcp-powerbi-sp';
END
ELSE PRINT 'User tcp-powerbi-sp already exists';

ALTER ROLE tcp_bi_reader ADD MEMBER [tcp-powerbi-sp];
PRINT 'Granted tcp_bi_reader role';

-- Verify
SELECT dp.name AS user_name,
       dp.type_desc,
       r.name AS role_name
FROM sys.database_principals dp
LEFT JOIN sys.database_role_members rm ON rm.member_principal_id = dp.principal_id
LEFT JOIN sys.database_principals r ON r.principal_id = rm.role_principal_id
WHERE dp.name = N'tcp-powerbi-sp';
