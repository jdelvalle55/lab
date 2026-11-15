-- =====================================================================
-- 03_seed_scope.sql  --  run as DBA. Edit the rows for your pilot, then run.
-- One row per user per branch. planner_an8 NULL = every planner in the branch.
-- =====================================================================
SET ECHO ON
WHENEVER SQLERROR EXIT FAILURE ROLLBACK

-- Lab pilot: JDE sign-on (AN8 1 in SY920.F0092), branch 30, all planners.
INSERT INTO JDEAGENT_OWN.AGENT_USER_SCOPE (jde_user, an8, mcu, planner_an8, agent_role)
VALUES ('JDE', 1, '30', NULL, 'PLANNER');

COMMIT;
SELECT * FROM JDEAGENT_OWN.AGENT_USER_SCOPE ORDER BY jde_user, mcu;
