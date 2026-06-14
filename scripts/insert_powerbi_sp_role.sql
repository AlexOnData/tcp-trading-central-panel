-- Insert PowerBI SP OID into dim_UserRoles with admin scope so RLS predicates
-- recognise the SP when PowerBI's dataset refresh reads via the SP's token.
-- Per ADR-003: "PowerBI SP registered with scope='admin'".

SET QUOTED_IDENTIFIER ON;

ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = OFF);

IF NOT EXISTS (
  SELECT 1 FROM dbo.dim_UserRoles
  WHERE aad_object_id = CAST('6d02d755-3e55-4afc-aab7-9ce4bcee04e1' AS UNIQUEIDENTIFIER)
    AND is_active = 1
)
BEGIN
  INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active, created_at)
  VALUES (
    CAST('6d02d755-3e55-4afc-aab7-9ce4bcee04e1' AS UNIQUEIDENTIFIER),
    NULL,
    'admin',
    1,
    SYSDATETIMEOFFSET()
  );
  PRINT 'Inserted tcp-powerbi-sp with admin scope';
END
ELSE PRINT 'Already exists';

ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON);

-- Verify
SELECT aad_object_id, scope, employee_id, is_active
FROM dbo.dim_UserRoles
WHERE aad_object_id = CAST('6d02d755-3e55-4afc-aab7-9ce4bcee04e1' AS UNIQUEIDENTIFIER);
