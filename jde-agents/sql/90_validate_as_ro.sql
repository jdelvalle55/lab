-- =====================================================================
-- 90_validate_as_ro.sql  --  CONNECT JDEAGENT_RO/<pwd>@JDV920
-- Proves (1) fail-closed with no identifier, (2) data with a scoped user,
-- (3) no access to base tables.
-- =====================================================================
SET ECHO ON
-- 1. No identifier: every count must be 0.
EXEC DBMS_SESSION.CLEAR_IDENTIFIER;
SELECT 'planning' v, COUNT(*) FROM JDEAGENT_OWN.V_ITEM_PLANNING
UNION ALL SELECT 'usage', COUNT(*) FROM JDEAGENT_OWN.V_ITEM_USAGE_WEEKLY
UNION ALL SELECT 'mrp',   COUNT(*) FROM JDEAGENT_OWN.V_MRP_EXCEPTIONS;

-- 2. Scoped user: counts > 0, only the branches in AGENT_USER_SCOPE.
EXEC DBMS_SESSION.SET_IDENTIFIER('POUSER');
SELECT mcu, COUNT(*) FROM JDEAGENT_OWN.V_ITEM_PLANNING GROUP BY mcu;
SELECT * FROM JDEAGENT_OWN.V_MRP_EXCEPTIONS FETCH FIRST 10 ROWS ONLY;
SET TIMING ON
SELECT COUNT(*) FROM JDEAGENT_OWN.V_ITEM_USAGE_WEEKLY;   -- watch the elapsed time
SET TIMING OFF

-- 3. Base table must fail with ORA-00942 / ORA-01031.
SELECT COUNT(*) FROM TESTDTA.F4102;
