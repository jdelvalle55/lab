-- =====================================================================
-- 01_owner_objects.sql  --  run as DBA with ALTER SESSION SET CURRENT_SCHEMA,
-- or via a proxy connect: CONNECT dba_user[JDEAGENT_OWN]/pwd@JDV920
-- Creates helper functions, config, scope and audit tables in JDEAGENT_OWN.
-- =====================================================================
SET ECHO ON
WHENEVER SQLERROR EXIT FAILURE ROLLBACK
ALTER SESSION SET CURRENT_SCHEMA = JDEAGENT_OWN;

-- JDE Julian (CYYDDD) <-> DATE. 0/NULL -> NULL. 125318 = 2025-11-14.
CREATE OR REPLACE FUNCTION JDEAGENT_OWN.JDE_TO_DATE (p_jde IN NUMBER) RETURN DATE
  DETERMINISTIC PARALLEL_ENABLE IS
BEGIN
  IF p_jde IS NULL OR p_jde <= 0 THEN RETURN NULL; END IF;
  RETURN TO_DATE(TO_CHAR(1900000 + p_jde), 'YYYYDDD');
END;
/
CREATE OR REPLACE FUNCTION JDEAGENT_OWN.DATE_TO_JDE (p_date IN DATE) RETURN NUMBER
  DETERMINISTIC PARALLEL_ENABLE IS
BEGIN
  IF p_date IS NULL THEN RETURN 0; END IF;
  RETURN (TO_NUMBER(TO_CHAR(p_date, 'YYYY')) - 1900) * 1000 + TO_NUMBER(TO_CHAR(p_date, 'DDD'));
END;
/

-- Settings the views read. Quantities in JDE are stored as integers with
-- implied decimals; set QTY_DIVISOR after comparing one item against P41026.
CREATE TABLE JDEAGENT_OWN.AGENT_CONFIG (
  cfg_key   VARCHAR2(40)  PRIMARY KEY,
  cfg_value VARCHAR2(400) NOT NULL,
  note      VARCHAR2(400)
);
INSERT INTO JDEAGENT_OWN.AGENT_CONFIG VALUES ('QTY_DIVISOR', '1',
  'Divide stored quantities by this (10^display decimals). Validate vs P41026.');
INSERT INTO JDEAGENT_OWN.AGENT_CONFIG VALUES ('USAGE_DCT', 'SO,IM,II',
  'F4111 doc types counted as usage (issues). Adjust to your site.');
INSERT INTO JDEAGENT_OWN.AGENT_CONFIG VALUES ('USAGE_DAYS', '91',
  'Usage history window in days (13 weeks).');

-- Who may see what. SQL bypasses JDE row security, so every view filters on this.
-- The MCP server sets CLIENT_IDENTIFIER to the JDE user before each query.
-- No identifier or no row here = no data (fail closed).
CREATE TABLE JDEAGENT_OWN.AGENT_USER_SCOPE (
  jde_user     VARCHAR2(10) NOT NULL,         -- JDE sign-on, e.g. POUSER
  an8          NUMBER       NOT NULL,         -- user's address number, e.g. 61049
  mcu          VARCHAR2(12) NOT NULL,         -- branch/plant, stored trimmed
  planner_an8  NUMBER,                        -- NULL = all planners in this branch
  agent_role   VARCHAR2(20) DEFAULT 'PLANNER' NOT NULL, -- PLANNER | WAREHOUSE | ADMIN
  CONSTRAINT agent_user_scope_pk PRIMARY KEY (jde_user, mcu, agent_role)
);

-- Write-path audit (used from phase 1b; created now so the schema is complete).
CREATE TABLE JDEAGENT_OWN.AGENT_AUDIT (
  audit_id     NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  created_at   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  jde_user     VARCHAR2(10) NOT NULL,
  tool_name    VARCHAR2(60) NOT NULL,
  phase        VARCHAR2(10) NOT NULL,  -- PREVIEW | CONFIRM | RESULT
  request_json CLOB,
  response_json CLOB
);

COMMIT;
