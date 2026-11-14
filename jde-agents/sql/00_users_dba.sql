-- =====================================================================
-- 00_users_dba.sql  --  run as a DBA (SYSTEM or equivalent) in the JDV920 DB
-- Creates the owner/read-only schema pair for the JDE Planner/Warehouse agents.
-- Mirrors the POAGENT_OWN / POAGENT_RO pattern. Does not touch POAGENT_*.
--
-- BACKUP FIRST: this is additive (new users only), but take a Data Pump
-- metadata export of the DBA-level grants if your change process requires it:
--   expdp system schemas=TESTDTA content=METADATA_ONLY directory=DATA_PUMP_DIR \
--         dumpfile=pre_jdeagent_testdta_meta.dmp logfile=pre_jdeagent.log
-- Rollback: 99_rollback_dba.sql
-- =====================================================================
SET ECHO ON
WHENEVER SQLERROR EXIT FAILURE ROLLBACK

DEFINE ro_password = '&1'
DEFINE ts = 'USERS'          -- tablespace for the small config/scope/audit tables

-- Profile: cap sessions and idle time for the agent's read-only login.
CREATE PROFILE JDEAGENT_PROFILE LIMIT
  SESSIONS_PER_USER        10
  IDLE_TIME                30
  FAILED_LOGIN_ATTEMPTS    5
  PASSWORD_LOCK_TIME       1/24
  PASSWORD_LIFE_TIME       UNLIMITED;   -- service account; rotate via vault/runbook

-- Owner: schema-only account (no password, cannot log in). Requires 18c+.
-- On 12c replace NO AUTHENTICATION with IDENTIFIED BY <random> ACCOUNT LOCK.
CREATE USER JDEAGENT_OWN NO AUTHENTICATION
  DEFAULT TABLESPACE &ts QUOTA 200M ON &ts;
GRANT CREATE TABLE, CREATE VIEW, CREATE PROCEDURE, CREATE MATERIALIZED VIEW TO JDEAGENT_OWN;

-- Read-only login used by the MCP server.
CREATE USER JDEAGENT_RO IDENTIFIED BY "&ro_password"
  DEFAULT TABLESPACE &ts QUOTA 0 ON &ts
  PROFILE JDEAGENT_PROFILE;
GRANT CREATE SESSION TO JDEAGENT_RO;

-- Owner needs SELECT ... WITH GRANT OPTION so RO can read views built on TESTDTA.
-- Only the tables the views use. Add F41021/F4108/F4111 etc. as phases need them.
GRANT SELECT ON TESTDTA.F4101  TO JDEAGENT_OWN WITH GRANT OPTION;  -- item master
GRANT SELECT ON TESTDTA.F4102  TO JDEAGENT_OWN WITH GRANT OPTION;  -- item branch
GRANT SELECT ON TESTDTA.F41021 TO JDEAGENT_OWN WITH GRANT OPTION;  -- item location (on hand)
GRANT SELECT ON TESTDTA.F4111  TO JDEAGENT_OWN WITH GRANT OPTION;  -- item ledger (usage)
GRANT SELECT ON TESTDTA.F3411  TO JDEAGENT_OWN WITH GRANT OPTION;  -- MRP/MPS messages

-- Verify
SELECT username, account_status, authentication_type, profile
  FROM dba_users WHERE username LIKE 'JDEAGENT%';
SELECT grantee, owner, table_name, privilege, grantable
  FROM dba_tab_privs WHERE grantee = 'JDEAGENT_OWN' ORDER BY table_name;
