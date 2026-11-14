-- =====================================================================
-- 03_seed_scope.sql  --  run as DBA. Edit the rows for your pilot, then run.
-- One row per user per branch. planner_an8 NULL = every planner in the branch.
-- =====================================================================
SET ECHO ON
WHENEVER SQLERROR EXIT FAILURE ROLLBACK

-- Example only: replace user, AN8 and branch with real pilot values.
INSERT INTO JDEAGENT_OWN.AGENT_USER_SCOPE (jde_user, an8, mcu, planner_an8, agent_role)
VALUES ('POUSER', 61049, '30', NULL, 'PLANNER');

COMMIT;
SELECT * FROM JDEAGENT_OWN.AGENT_USER_SCOPE ORDER BY jde_user, mcu;
