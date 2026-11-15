-- =====================================================================
-- 04_leadtime_views.sql  --  run as DBA. Adds actual supplier lead times.
-- Lead time per receipt = receipt date (F43121.PRRCDJ) - PO order date (F4311.PDTRDJ).
-- Additive only: two grants, one config row, two views, two RO grants.
-- Rollback: 98_rollback_leadtime.sql
-- =====================================================================
SET ECHO ON SERVEROUTPUT ON
WHENEVER SQLERROR EXIT FAILURE ROLLBACK

-- PO detail and receipt ledger
GRANT SELECT ON TESTDTA.F4311  TO JDEAGENT_OWN WITH GRANT OPTION;
GRANT SELECT ON TESTDTA.F43121 TO JDEAGENT_OWN WITH GRANT OPTION;

ALTER SESSION SET CURRENT_SCHEMA = JDEAGENT_OWN;

-- ---------- Column precheck ----------
DECLARE
  TYPE t_cols IS TABLE OF VARCHAR2(80);
  l_req t_cols := t_cols(
    'F4311.PDDOCO','F4311.PDDCTO','F4311.PDKCOO','F4311.PDSFXO','F4311.PDLNID',
    'F4311.PDTRDJ','F4311.PDAN8',
    'F43121.PRDOCO','F43121.PRDCTO','F43121.PRKCOO','F43121.PRSFXO','F43121.PRLNID',
    'F43121.PRITM','F43121.PRMCU','F43121.PRAN8','F43121.PRRCDJ','F43121.PRMATC');
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

-- History window for lead time statistics (days back from AS_OF_DATE or today).
MERGE INTO JDEAGENT_OWN.AGENT_CONFIG c
USING (SELECT 'LEADTIME_DAYS' k FROM dual) s ON (c.cfg_key = s.k)
WHEN NOT MATCHED THEN INSERT (cfg_key, cfg_value, note)
  VALUES ('LEADTIME_DAYS', '730', 'Receipt history window for actual lead times (days).');
COMMIT;

-- ---------- V_SUPPLIER_LEADTIME: one row per item / branch / supplier ----------
-- Receipt rows only (PRMATC = '1'); one row per received PO line (earliest receipt date),
-- so partial receipts don't double-count. MMMSGA-style NCHAR handled with N'' literals.
CREATE OR REPLACE VIEW JDEAGENT_OWN.V_SUPPLIER_LEADTIME AS
WITH c AS (SELECT MAX(CASE cfg_key WHEN 'LEADTIME_DAYS' THEN TO_NUMBER(cfg_value) END) days,
                  NVL(MAX(CASE cfg_key WHEN 'AS_OF_DATE' THEN TO_DATE(cfg_value, 'YYYY-MM-DD') END),
                      TRUNC(SYSDATE)) as_of
             FROM JDEAGENT_OWN.AGENT_CONFIG),
     rcpt AS (
       SELECT r.PRITM itm, r.PRMCU mcu_raw, r.PRAN8 supplier_an8,
              r.PRDOCO, r.PRDCTO, r.PRKCOO, r.PRSFXO, r.PRLNID,
              MIN(r.PRRCDJ) rcdj
         FROM TESTDTA.F43121 r CROSS JOIN c
        WHERE TRIM(r.PRMATC) = N'1'
          AND r.PRRCDJ >  JDEAGENT_OWN.DATE_TO_JDE(c.as_of - c.days)
          AND r.PRRCDJ <= JDEAGENT_OWN.DATE_TO_JDE(c.as_of)
        GROUP BY r.PRITM, r.PRMCU, r.PRAN8, r.PRDOCO, r.PRDCTO, r.PRKCOO, r.PRSFXO, r.PRLNID),
     lines AS (
       SELECT rc.itm, rc.mcu_raw, rc.supplier_an8,
              JDEAGENT_OWN.JDE_TO_DATE(rc.rcdj) - JDEAGENT_OWN.JDE_TO_DATE(d.PDTRDJ) lt_days,
              JDEAGENT_OWN.JDE_TO_DATE(rc.rcdj) receipt_date
         FROM rcpt rc
         JOIN TESTDTA.F4311 d
           ON d.PDDOCO = rc.PRDOCO AND d.PDDCTO = rc.PRDCTO AND d.PDKCOO = rc.PRKCOO
          AND d.PDSFXO = rc.PRSFXO AND d.PDLNID = rc.PRLNID
        WHERE d.PDTRDJ > 0)
SELECT TRIM(l.mcu_raw)                                        AS mcu,
       l.itm                                                  AS itm,
       TRIM(b.IBLITM)                                         AS litm,
       b.IBANPL                                               AS planner_an8,
       l.supplier_an8                                         AS supplier_an8,
       COUNT(*)                                               AS receipts,
       ROUND(MEDIAN(l.lt_days), 1)                            AS median_lt_days,
       ROUND(AVG(l.lt_days), 1)                               AS avg_lt_days,
       ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY l.lt_days), 1) AS p90_lt_days,
       MIN(l.lt_days)                                         AS min_lt_days,
       MAX(l.lt_days)                                         AS max_lt_days,
       b.IBLTLV                                               AS planned_lt_days,
       MAX(l.receipt_date)                                    AS last_receipt_date
  FROM lines l
  JOIN TESTDTA.F4102 b ON b.IBITM = l.itm AND b.IBMCU = l.mcu_raw
 WHERE l.lt_days >= 0
   AND EXISTS (SELECT 1 FROM JDEAGENT_OWN.AGENT_USER_SCOPE s
                WHERE s.jde_user = SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER')
                  AND s.agent_role IN ('PLANNER', 'ADMIN')
                  AND s.mcu = TRIM(l.mcu_raw)
                  AND (s.planner_an8 IS NULL OR s.planner_an8 = b.IBANPL))
 GROUP BY TRIM(l.mcu_raw), l.itm, TRIM(b.IBLITM), b.IBANPL, l.supplier_an8, b.IBLTLV;

-- ---------- V_ITEM_LEADTIME: actual lead time per item / branch (all suppliers) ----------
-- Built on V_SUPPLIER_LEADTIME's logic but across suppliers; inherits its scope filter.
CREATE OR REPLACE VIEW JDEAGENT_OWN.V_ITEM_LEADTIME AS
SELECT mcu, itm, litm,
       SUM(receipts)                                                AS receipts,
       COUNT(*)                                                     AS suppliers,
       ROUND(SUM(median_lt_days * receipts) / SUM(receipts), 1)     AS weighted_median_lt_days,
       MAX(p90_lt_days)                                             AS worst_p90_lt_days,
       MAX(planned_lt_days)                                         AS planned_lt_days,
       MAX(last_receipt_date)                                       AS last_receipt_date
  FROM JDEAGENT_OWN.V_SUPPLIER_LEADTIME
 GROUP BY mcu, itm, litm;

GRANT SELECT ON JDEAGENT_OWN.V_SUPPLIER_LEADTIME TO JDEAGENT_RO;
GRANT SELECT ON JDEAGENT_OWN.V_ITEM_LEADTIME     TO JDEAGENT_RO;

SELECT object_name, object_type, status FROM all_objects
 WHERE owner = 'JDEAGENT_OWN' AND object_type = 'VIEW' ORDER BY object_name;
