-- =====================================================================
-- 98_rollback_leadtime.sql  --  run as DBA. Removes only what 04 added.
-- =====================================================================
SET ECHO ON
DROP VIEW JDEAGENT_OWN.V_ITEM_LEADTIME;
DROP VIEW JDEAGENT_OWN.V_SUPPLIER_LEADTIME;
DELETE FROM JDEAGENT_OWN.AGENT_CONFIG WHERE cfg_key = 'LEADTIME_DAYS';
COMMIT;
REVOKE SELECT ON TESTDTA.F43121 FROM JDEAGENT_OWN;
REVOKE SELECT ON TESTDTA.F4311  FROM JDEAGENT_OWN;
