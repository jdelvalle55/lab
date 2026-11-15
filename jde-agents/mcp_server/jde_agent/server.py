"""JDE Planner agent MCP server (phase 1: read-only).

Run:  python -m jde_agent.server      (env from .env, see .env.example)
Tools never accept SQL and never write to JDE. Writes arrive in phase 1b via
AIS orchestrations with preview -> confirm, like the PO approval agent.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from mcp.server.fastmcp import FastMCP

from . import calc, db
from .config import Settings

S = Settings()
mcp = FastMCP("jde-planner-agent", host=S.host, port=S.port)

# F3411 message types: only the codes we're confident of are labelled. Anything else is
# reported as-is with "See UDC"; confirm the full list in your MRP message type UDC.
MESSAGE_TYPES = {
    "B": "Order and expedite", "C": "Cancel", "D": "Defer", "E": "Expedite",
    "G": "Increase order quantity", "H": "Decrease order quantity", "O": "Order",
}


def _jsonable(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({k: (v.date().isoformat() if isinstance(v, datetime)
                        else v.isoformat() if isinstance(v, date)
                        else v.strip() if isinstance(v, str) else v) for k, v in r.items()})
    return out


def _order_rules(it: dict) -> dict:
    return {"min_qty": float(it.get("min_reorder_qty") or 0),
            "max_qty": float(it.get("max_reorder_qty") or 0),
            "multiple": float(it.get("order_multiple") or 0)}


def _usage_by_item(branch: str, itm: int | None = None) -> dict[int, list[float]]:
    sql = ("SELECT itm, week_start, usage_qty FROM JDEAGENT_OWN.V_ITEM_USAGE_WEEKLY "
           "WHERE mcu = :mcu" + (" AND itm = :itm" if itm is not None else "") +
           " ORDER BY itm, week_start")
    params = {"mcu": branch.strip()} | ({"itm": itm} if itm is not None else {})
    usage: dict[int, list[float]] = defaultdict(list)
    for r in db.query(S, S.jde_user, sql, params, max_rows=200_000):
        usage[r["itm"]].append(float(r["usage_qty"] or 0))
    return usage


@mcp.tool()
def find_param_gaps(branch: str, planner_an8: int | None = None, limit: int = 25) -> dict:
    """Find stocked items in a branch/plant whose safety stock, reorder point or lead time
    is missing or out of line with the last 13 weeks of usage. Ranked by average daily usage.
    Read-only: does not change JDE. Does not cover items outside the caller's scope.

    branch: JDE branch/plant (MCU), e.g. "30". planner_an8: optional planner filter.
    """
    limit = max(1, min(limit, 100))
    sql = ("SELECT itm, litm, description, planner_an8, stocking_type, safety_stock, reorder_point, "
           "reorder_qty, min_reorder_qty, max_reorder_qty, order_multiple, lead_time_days, "
           "on_hand, committed_qty, on_po "
           "FROM JDEAGENT_OWN.V_ITEM_PLANNING WHERE mcu = :mcu")
    params: dict = {"mcu": branch.strip()}
    if planner_an8 is not None:
        sql += " AND planner_an8 = :pl"
        params["pl"] = planner_an8
    items = [i for i in db.query(S, S.jde_user, sql, params, max_rows=50_000)
             if (i["stocking_type"] or "").strip() in S.planned_stocking_types]
    usage = _usage_by_item(branch)
    findings = []
    for it in items:
        rec = calc.recommend(usage.get(it["itm"], []), float(it["lead_time_days"] or 0),
                             weeks=S.usage_weeks, z=S.service_level_z,
                             review_period_days=S.review_period_days, **_order_rules(it))
        gaps = calc.find_gaps(it, rec)
        if gaps:
            findings.append({"item": it["litm"], "description": it["description"],
                             "planner_an8": it["planner_an8"],
                             "current": {k: it[k] for k in ("safety_stock", "reorder_point",
                                                            "reorder_qty", "lead_time_days")},
                             "suggested": rec.as_dict(),
                             "gaps": [{"code": g, "text": calc.GAP_TEXT[g]} for g in gaps]})
    findings.sort(key=lambda f: f["suggested"]["avg_daily_usage"], reverse=True)
    counts: dict[str, int] = defaultdict(int)
    for f in findings:
        for g in f["gaps"]:
            counts[g["code"]] += 1
    return {"branch": branch, "items_checked": len(items), "items_with_gaps": len(findings),
            "gap_counts": dict(counts), "top": _jsonable(findings[:limit])}


@mcp.tool()
def recommend_reorder(item: str, branch: str) -> dict:
    """Suggest safety stock, reorder point and reorder quantity for one item/branch from
    13 weeks of usage and the item-branch lead time. Shows the inputs so a planner can check
    the math. Read-only: it does not update F4102.

    item: 2nd item number (LITM). branch: branch/plant (MCU).
    """
    rows = db.query(S, S.jde_user,
                    "SELECT * FROM JDEAGENT_OWN.V_ITEM_PLANNING WHERE mcu = :mcu AND litm = :litm",
                    {"mcu": branch.strip(), "litm": item.strip()}, max_rows=1)
    if not rows:
        return {"error": "Item/branch not found or not in your scope."}
    it = rows[0]
    weekly = _usage_by_item(branch, it["itm"]).get(it["itm"], [])
    rec = calc.recommend(weekly, float(it["lead_time_days"] or 0), weeks=S.usage_weeks,
                         z=S.service_level_z, review_period_days=S.review_period_days,
                         **_order_rules(it))
    available = float(it["on_hand"] or 0) - float(it["committed_qty"] or 0)
    position = available + float(it["on_po"] or 0)
    return {
        "item": it["litm"], "description": it["description"], "branch": branch,
        "current": _jsonable([{k: it[k] for k in ("safety_stock", "reorder_point", "reorder_qty",
                                                  "min_reorder_qty", "max_reorder_qty",
                                                  "order_multiple", "lead_time_days", "on_hand",
                                                  "committed_qty", "on_po")}])[0],
        "suggested": rec.as_dict(),
        "inventory_position": round(position, 2),
        "below_suggested_rop": bool(rec.reorder_point is not None and position < rec.reorder_point),
        "gaps": [{"code": g, "text": calc.GAP_TEXT[g]} for g in calc.find_gaps(it, rec)],
        "method": {"weeks": S.usage_weeks, "z": S.service_level_z,
                   "review_period_days": S.review_period_days, "weekly_usage": weekly},
    }


@mcp.tool()
def list_mrp_exceptions(branch: str, message_type: str | None = None,
                        min_days_late: int = 0, limit: int = 50) -> dict:
    """List open MRP/MPS messages (F3411) for a branch, grouped by message type and ranked by
    days late. Read-only: it does not process, hold or clear messages.

    message_type: optional single code, e.g. "E" (expedite). min_days_late: filter on
    requested date before today.
    """
    limit = max(1, min(limit, 200))
    sql = ("SELECT message_id, litm, description, planner_an8, message_type, action_code, order_no, "
           "order_type, requested_date, days_late, qty FROM JDEAGENT_OWN.V_MRP_EXCEPTIONS "
           "WHERE mcu = :mcu AND days_late >= :late")
    params: dict = {"mcu": branch.strip(), "late": max(0, min_days_late)}
    if message_type:
        sql += " AND message_type = :mt"
        params["mt"] = message_type.strip().upper()
    sql += " ORDER BY days_late DESC, qty DESC"
    rows = _jsonable(db.query(S, S.jde_user, sql, params, max_rows=5_000))
    summary: dict[str, dict] = {}
    for r in rows:
        mt = r["message_type"] or "?"
        s = summary.setdefault(mt, {"label": MESSAGE_TYPES.get(mt, "See UDC"), "count": 0,
                                    "max_days_late": 0})
        s["count"] += 1
        s["max_days_late"] = max(s["max_days_late"], r["days_late"] or 0)
        r["message_label"] = MESSAGE_TYPES.get(mt, "See UDC")
    return {"branch": branch, "total": len(rows), "by_type": summary, "top": rows[:limit]}


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
