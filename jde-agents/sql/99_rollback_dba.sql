-- =====================================================================
-- 99_rollback_dba.sql  --  run as DBA. Removes everything 00-03 created.
-- Touches only JDEAGENT_* objects; TESTDTA data and POAGENT_* are untouched.
-- =====================================================================
SET ECHO ON
DROP USER JDEAGENT_RO  CASCADE;
-- drops views, functions, config/scope/audit tables
DROP USER JDEAGENT_OWN CASCADE;
DROP PROFILE JDEAGENT_PROFILE;
