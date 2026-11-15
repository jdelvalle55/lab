-- =====================================================================
-- 02_planner_views.sql  --  run as DBA (CURRENT_SCHEMA = JDEAGENT_OWN)
-- Three Planner views + grants to JDEAGENT_RO.
-- Step 0 checks that every JDE column used exists in TESTDTA and stops if not.
-- Column names marked VERIFY are from the standard 9.2 data dictionary; the
-- precheck will tell you if your release differs.
-- =====================================================================
SET ECHO ON SERVEROUTPUT ON
WHENEVER SQLERROR EXIT FAILURE ROLLBACK
ALTER SESSION SET CURRENT_SCHEMA = JDEAGENT_OWN;

-- ---------- Step 0: column precheck ----------
DECLARE
  TYPE t_cols IS TABLE OF VARCHAR2(80);
  l_req t_cols := t_cols(
    'F4101.IMITM','F4101.IMLITM','F4101.IMDSC1',
    'F4102.IBITM','F4102.IBLITM','F4102.IBMCU','F4102.IBSTKT','F4102.IBMPST',
    'F4102.IBANPL','F4102.IBBUYR','F4102.IBSAFE','F4102.IBROPI','F4102.IBROQI',
    'F4102.IBLTLV','F4102.IBRQMN','F4102.IBRQMX','F4102.IBMULT','F4102.IBABCS','F4102.IBABCM','F4102.IBABCI','F4102.IBCYCL',
    'F41021.LIITM','F41021.LIMCU','F41021.LIPQOH','F41021.LIHCOM','F41021.LIPCOM','F41021.LIPREQ',
    'F4111.ILITM','F4111.ILMCU','F4111.ILDCT','F4111.ILTRQT','F4111.ILTRDJ',
    'F3411.MMUKID','F3411.MMITM','F3411.MMMCU','F3411.MMMSGT','F3411.MMMSGA',
    'F3411.MMDOCO','F3411.MMDCTO','F3411.MMDRQJ','F3411.MMTRQT');
  l_cnt NUMBER; l_missing NUMBER := 0;
BEGIN
  FOR i IN 1 .. l_req.COUNT LOOP
    SELECT COUNT(*) INTO l_cnt FROM all_tab_columns
     WHERE owner = 'TESTDTA'
       AND table_name  = SUBSTR(l_req(i), 1, INSTR(l_req(i), '.') - 1)
       AND column_name = SUBSTR(l_req(i), INSTR(l_req(i), '.') + 1);
    IF l_cnt = 0 THEN
      DBMS_OUTPUT.PUT_LINE('MISSING: TESTDTA.' || l_req(i));
      l_missing := l_missing + 1;
    END IF;
  END LOOP;
  IF l_missing > 0 THEN
    RAISE_APPLICATION_ERROR(-20001, l_missing || ' column(s) missing; fix the view SQL before continuing');
  END IF;
  DBMS_OUTPUT.PUT_LINE('Column precheck passed');
END;
/

-- ---------- V_ITEM_PLANNING: item/branch planning values + stock position ----------
CREATE OR REPLACE VIEW JDEAGENT_OWN.V_ITEM_PLANNING AS
WITH q AS (SELECT TO_NUMBER(cfg_value) d FROM JDEAGENT_OWN.AGENT_CONFIG WHERE cfg_key = 'QTY_DIVISOR'),
     loc AS (SELECT LIITM, LIMCU,
                    SUM(LIPQOH) on_hand, SUM(LIHCOM + LIPCOM) committed_qty, SUM(LIPREQ) on_po
               FROM TESTDTA.F41021 GROUP BY LIITM, LIMCU)
SELECT TRIM(b.IBMCU)              AS mcu,
       b.IBITM                    AS itm,
       TRIM(b.IBLITM)             AS litm,
       TRIM(m.IMDSC1)             AS description,
       b.IBSTKT                   AS stocking_type,
       b.IBMPST                   AS planning_code,
       b.IBANPL                   AS planner_an8,
       b.IBBUYR                   AS buyer_an8,
       b.IBSAFE / q.d             AS safety_stock,
       b.IBROPI / q.d             AS reorder_point,
       b.IBROQI / q.d             AS reorder_qty,
       b.IBRQMN / q.d             AS min_reorder_qty,
       b.IBRQMX / q.d             AS max_reorder_qty,
       b.IBMULT / q.d             AS order_multiple,
       b.IBLTLV                   AS lead_time_days,
       b.IBABCS                   AS abc_sales,
       b.IBABCM                   AS abc_margin,
       b.IBABCI                   AS abc_investment,
       b.IBCYCL                   AS cycle_count_cat,
       NVL(loc.on_hand, 0) / q.d        AS on_hand,
       NVL(loc.committed_qty, 0) / q.d  AS committed_qty,
       NVL(loc.on_po, 0) / q.d          AS on_po
  FROM TESTDTA.F4102 b
  JOIN TESTDTA.F4101 m ON m.IMITM = b.IBITM
  CROSS JOIN q
  LEFT JOIN loc ON loc.LIITM = b.IBITM AND loc.LIMCU = b.IBMCU
 WHERE EXISTS (SELECT 1 FROM JDEAGENT_OWN.AGENT_USER_SCOPE s
                WHERE s.jde_user = SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER')
                  AND s.agent_role IN ('PLANNER', 'ADMIN')
                  AND s.mcu = TRIM(b.IBMCU)
                  AND (s.planner_an8 IS NULL OR s.planner_an8 = b.IBANPL));

-- ---------- V_ITEM_USAGE_WEEKLY: issues per item/branch/ISO week ----------
-- Optional AGENT_CONFIG 'AS_OF_DATE' (YYYY-MM-DD) moves 'today' back for stale lab data;
-- delete the row to use SYSDATE. Also drives days_late in V_MRP_EXCEPTIONS.
-- F4111 is the big table: this view filters on raw ILTRDJ so the date index can be used.
-- If a full-branch query takes more than a few seconds, convert to a nightly MV.
CREATE OR REPLACE VIEW JDEAGENT_OWN.V_ITEM_USAGE_WEEKLY AS
WITH c AS (SELECT MAX(CASE cfg_key WHEN 'QTY_DIVISOR' THEN TO_NUMBER(cfg_value) END) d,
                  MAX(CASE cfg_key WHEN 'USAGE_DCT'   THEN cfg_value END)            dct,
                  MAX(CASE cfg_key WHEN 'USAGE_DAYS'  THEN TO_NUMBER(cfg_value) END) days,
                  NVL(MAX(CASE cfg_key WHEN 'AS_OF_DATE' THEN TO_DATE(cfg_value, 'YYYY-MM-DD') END),
                      TRUNC(SYSDATE))                                                as_of
             FROM JDEAGENT_OWN.AGENT_CONFIG)
SELECT TRIM(l.ILMCU)                                    AS mcu,
       l.ILITM                                          AS itm,
       TRUNC(JDEAGENT_OWN.JDE_TO_DATE(l.ILTRDJ), 'IW')  AS week_start,
       SUM(-l.ILTRQT) / MAX(c.d)                        AS usage_qty
  FROM TESTDTA.F4111 l
  CROSS JOIN c
 WHERE l.ILTRDJ >  JDEAGENT_OWN.DATE_TO_JDE(c.as_of - c.days)
   AND l.ILTRDJ <= JDEAGENT_OWN.DATE_TO_JDE(c.as_of)
   AND l.ILTRQT < 0
   AND INSTR(',' || c.dct || ',', ',' || TRIM(l.ILDCT) || ',') > 0
   AND EXISTS (SELECT 1
                 FROM JDEAGENT_OWN.AGENT_USER_SCOPE s
                 JOIN TESTDTA.F4102 b ON b.IBITM = l.ILITM AND b.IBMCU = l.ILMCU
                WHERE s.jde_user = SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER')
                  AND s.agent_role IN ('PLANNER', 'ADMIN')
                  AND s.mcu = TRIM(l.ILMCU)
                  AND (s.planner_an8 IS NULL OR s.planner_an8 = b.IBANPL))
 GROUP BY TRIM(l.ILMCU), l.ILITM, TRUNC(JDEAGENT_OWN.JDE_TO_DATE(l.ILTRDJ), 'IW');

-- ---------- V_MRP_EXCEPTIONS: open MRP/MPS messages ----------
-- MMMSGA = Cleared flag ('Y' or blank); cleared messages are excluded.
-- MMMSGT/MMMSGA are NCHAR in a Unicode JDE install: compare with N'' literals.
CREATE OR REPLACE VIEW JDEAGENT_OWN.V_MRP_EXCEPTIONS AS
WITH q AS (SELECT MAX(CASE cfg_key WHEN 'QTY_DIVISOR' THEN TO_NUMBER(cfg_value) END) d,
                  NVL(MAX(CASE cfg_key WHEN 'AS_OF_DATE' THEN TO_DATE(cfg_value, 'YYYY-MM-DD') END),
                      TRUNC(SYSDATE)) as_of
             FROM JDEAGENT_OWN.AGENT_CONFIG)
SELECT x.MMUKID                                AS message_id,
       TRIM(x.MMMCU)                           AS mcu,
       x.MMITM                                 AS itm,
       TRIM(b.IBLITM)                          AS litm,
       TRIM(m.IMDSC1)                          AS description,
       b.IBANPL                                AS planner_an8,
       TRIM(x.MMMSGT)                          AS message_type,
       TRIM(x.MMMSGA)                          AS action_code,
       x.MMDOCO                                AS order_no,
       TRIM(x.MMDCTO)                          AS order_type,
       JDEAGENT_OWN.JDE_TO_DATE(x.MMDRQJ)      AS requested_date,
       GREATEST(q.as_of - JDEAGENT_OWN.JDE_TO_DATE(x.MMDRQJ), 0) AS days_late,
       x.MMTRQT / q.d                          AS qty
  FROM TESTDTA.F3411 x
  JOIN TESTDTA.F4102 b ON b.IBITM = x.MMITM AND b.IBMCU = x.MMMCU
  JOIN TESTDTA.F4101 m ON m.IMITM = x.MMITM
  CROSS JOIN q
 WHERE NVL(TRIM(x.MMMSGA), N' ') <> N'Y'
   AND EXISTS (SELECT 1 FROM JDEAGENT_OWN.AGENT_USER_SCOPE s
                WHERE s.jde_user = SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER')
                  AND s.agent_role IN ('PLANNER', 'ADMIN')
                  AND s.mcu = TRIM(x.MMMCU)
                  AND (s.planner_an8 IS NULL OR s.planner_an8 = b.IBANPL));

-- ---------- Grants: RO sees the three views and nothing else ----------
GRANT SELECT ON JDEAGENT_OWN.V_ITEM_PLANNING     TO JDEAGENT_RO;
GRANT SELECT ON JDEAGENT_OWN.V_ITEM_USAGE_WEEKLY TO JDEAGENT_RO;
GRANT SELECT ON JDEAGENT_OWN.V_MRP_EXCEPTIONS    TO JDEAGENT_RO;

SELECT object_name, object_type, status FROM all_objects
 WHERE owner = 'JDEAGENT_OWN' ORDER BY object_type, object_name;
