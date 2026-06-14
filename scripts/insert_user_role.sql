-- Insert the deployer's AAD OID into dim_UserRoles as admin so they can
-- use the chatbot. Replace the placeholder OID below with your own AAD object ID.
-- Idempotent: skip if already present.
ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = OFF);

IF NOT EXISTS (
  SELECT 1 FROM dbo.dim_UserRoles
  WHERE aad_object_id = CAST('00000000-0000-0000-0000-000000000000' AS UNIQUEIDENTIFIER)
    AND is_active = 1
)
BEGIN
  INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active, created_at)
  VALUES (CAST('00000000-0000-0000-0000-000000000000' AS UNIQUEIDENTIFIER), NULL, 'admin', 1, SYSDATETIMEOFFSET());
  PRINT 'Inserted deployer with admin scope.';
END
ELSE PRINT 'Already exists.';

ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON);
