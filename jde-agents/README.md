# JDE Planner agent: phase 1 read layer and MCP tools

The first slice of step 2 from the architecture doc: a read-only schema pair over TESTDTA (JDV920), three Planner views, and an MCP server with three tools. It follows the PO approval agent pattern: views behind a read-only user, MCP on vmwindows, and no writes to JDE.

| Piece | What it is |
| --- | --- |
| `sql/00_users_dba.sql` | Profile `JDEAGENT_PROFILE`, schema-only owner `JDEAGENT_OWN`, login `JDEAGENT_RO`, SELECT WITH GRANT OPTION on F4101/F4102/F41021/F4111/F3411 |
| `sql/01_owner_objects.sql` | Julian date functions, `AGENT_CONFIG`, `AGENT_USER_SCOPE`, `AGENT_AUDIT` |
| `sql/02_planner_views.sql` | Column precheck, then `V_ITEM_PLANNING`, `V_ITEM_USAGE_WEEKLY`, `V_MRP_EXCEPTIONS`, grants to RO |
| `sql/03_seed_scope.sql` | Pilot user/branch rows (edit first) |
| `sql/04_leadtime_views.sql` | Grants on F4311/F43121, `V_SUPPLIER_LEADTIME` and `V_ITEM_LEADTIME` (actual PO-to-receipt lead times), `LEADTIME_DAYS` config |
| `sql/98_rollback_leadtime.sql` | Removes only what 04 added |
| `sql/90_validate_as_ro.sql` | Fail-closed, scoped-data, and no-base-table checks |
| `sql/99_rollback_dba.sql` | Drops everything above |
| `mcp_server/` | Python MCP server (FastMCP, streamable HTTP, default port 8082) |
| `tests/` | Unit tests for the planning math |

## Tools

| Tool | Reads | Returns |
| --- | --- | --- |
| `find_param_gaps(branch, planner_an8?, limit?)` | V_ITEM_PLANNING, V_ITEM_USAGE_WEEKLY | Items with missing or misaligned SS/ROP/lead time, ranked by usage |
| `recommend_reorder(item, branch)` | same | Suggested SS/ROP/ROQ with formula inputs and inventory position |
| `supplier_leadtime_drift(branch, min_receipts?, drift_pct?, limit?)` | V_SUPPLIER_LEADTIME | Item/supplier pairs whose actual median lead time differs from IBLTLV, plus items with receipts but no planned lead time |
| `list_mrp_exceptions(branch, message_type?, min_days_late?, limit?)` | V_MRP_EXCEPTIONS | Open F3411 messages by type, ranked by days late |

Math lives in `mcp_server/jde_agent/calc.py`: 13 weeks of usage (missing weeks = 0), SS = z × σ_daily × √LT, ROP = daily usage × LT + SS, ROQ = daily usage × 30. The LLM never computes these. Lead time used for sizing: actual median from receipts when there are at least `MIN_LT_RECEIPTS` (default 3), else IBLTLV, else `DEFAULT_LEAD_TIME_DAYS`; `lead_time.source` in each result says which (`actual`, `item_branch`, `assumed`). When an item branch has no lead time (IBLTLV = 0), `DEFAULT_LEAD_TIME_DAYS` (default 14) is used and the result is flagged `lead_time_assumed` with an ASSUMED note; set it to 0 to turn that off.

## Deploy (lab, JDV920)

**Backup first.** The scripts only add new users and objects; nothing in TESTDTA or POAGENT_* changes. If your change process requires it, take a metadata export of the TESTDTA grants beforehand (command at the top of `00_users_dba.sql`).

1. SQL*Plus note: a `;` only ends a statement when it is the last thing on the line. Keep comments on their own line, never after a `;`.
2. Precheck the columns. `02_planner_views.sql` stops with a list of missing columns if your release differs. In JDV920, F3411 quantity is `MMTRQT` and `MMMSGA` is the cleared flag (`Y`/blank).
3. Run as a DBA, from SQL*Plus or SQLcl:
   ```
   sqlplus system@JDV920 @sql/00_users_dba.sql '<strong RO password>'
   sqlplus system@JDV920 @sql/01_owner_objects.sql
   sqlplus system@JDV920 @sql/02_planner_views.sql
   -- edit sql/03_seed_scope.sql with the pilot user, AN8 and branch, then:
   sqlplus system@JDV920 @sql/03_seed_scope.sql
   sqlplus system@JDV920 @sql/04_leadtime_views.sql
   ```
4. Calibrate the quantity scaling. Compare one item's safety stock in `V_ITEM_PLANNING` with P41026. If they differ by 10^n, run:
   `UPDATE JDEAGENT_OWN.AGENT_CONFIG SET cfg_value = '<10^n>' WHERE cfg_key = 'QTY_DIVISOR'; COMMIT;`
   Do the same for `USAGE_DCT` if your issue doc types aren't SO/IM/II.
   Lab data is old? Insert `('AS_OF_DATE', 'YYYY-MM-DD', ...)` into `AGENT_CONFIG` so the usage window and days-late are measured from that date. Delete the row for live data.
5. On vmwindows, set up the server:
   ```powershell
   cd <repo>\jde-agents\mcp_server
   py -3 -m venv .venv          # 3.12+; tested with 3.14 on vmwindows
   .\.venv\Scripts\pip install -r requirements.txt
   copy .env.example .env   # fill in DSN, RO password, ORACLE_CLIENT_LIB (thick mode for NNE), JDE_AGENT_USER
   .\run_server.ps1
   ```
   Keep `MCP_HOST=127.0.0.1` unless Claude Code connects from another machine. In that case, bind to the VM's IP and add an inbound Windows Firewall rule for TCP 8082 limited to that client's IP. Also open 8082 in the OCI security list or NSG for that source only.
6. Add the server to Claude Code:
   `claude mcp add --transport http jde-planner http://<vmwindows>:8082/mcp`

## Validation

- Run `sql/90_validate_as_ro.sql` as JDEAGENT_RO. With no identifier every count is 0. With `POUSER` you see only the seeded branches. `TESTDTA.F4102` fails with ORA-00942.
- Check the timing on `V_ITEM_USAGE_WEEKLY`. If one branch takes more than about 5 s, convert it to a nightly materialized view, or check that `F4111` has an index leading on ILTRDJ (or ILITM, ILMCU, ILTRDJ).
- From Claude Code, ask "Which items in branch 30 have missing reorder points?" and "Recommend reorder values for item X in branch 30". Then spot-check one item's usage against the item ledger (P4111).
- Run unit tests with `pip install pytest && pytest tests`.

## Risks / rollback

- **Row scoping depends on CLIENT_IDENTIFIER.** The views fail closed when it's unset, but a bad row in `AGENT_USER_SCOPE` exposes that branch. Test with a second user before the pilot. If the PO agent scopes users a different way, align this to match (open question in the doc).
- **F4111 scans can load the DV database.** Consider a Resource Manager consumer group for JDEAGENT_RO.
- **MRP message labels:** only B/C/D/E/G/H/O are labelled; other codes (A, N, P, T...) show as "See UDC" until confirmed.
- **Rollback:** stop the server, remove it from Claude Code, and run `sql/99_rollback_dba.sql`. This takes under a minute and touches only JDEAGENT_* objects.
