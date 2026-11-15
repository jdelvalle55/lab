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
    return {"default_lead_time_days": S.default_lead_time_days,
            "min_qty": float(it.get("min_reorder_qty") or 0),
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


def _actual_lead_times(branch: str, itm: int | None = None) -> dict[int, dict]:
    sql = ("SELECT itm, receipts, suppliers, weighted_median_lt_days, worst_p90_lt_days, "
           "last_receipt_date FROM JDEAGENT_OWN.V_ITEM_LEADTIME WHERE mcu = :mcu"
           + (" AND itm = :itm" if itm is not None else ""))
    params = {"mcu": branch.strip()} | ({"itm": itm} if itm is not None else {})
    return {r["itm"]: r for r in db.query(S, S.jde_user, sql, params, max_rows=50_000)}


def _size(it: dict, weekly: list[float], actual: dict | None):
    """Resolve the lead time (actual > item branch > assumed default) and size the item."""
    planned = float(it["lead_time_days"] or 0)
    act_days = float(actual["weighted_median_lt_days"]) if actual and actual["weighted_median_lt_days"] is not None else None
    receipts = int(actual["receipts"]) if actual else 0
    lt, source = calc.choose_lead_time(planned, act_days, receipts, S.min_lt_receipts)
    rec = calc.recommend(weekly, lt, weeks=S.usage_weeks, z=S.service_level_z,
                         review_period_days=S.review_period_days, **_order_rules(it))
    if source == "none":
        source = "assumed" if rec.lead_time_assumed else "none"
    drift = calc.lead_time_drift(planned, act_days, receipts, S.min_lt_receipts, S.lt_drift_pct)
    lt_info = {"source": source, "used_days": rec.lead_time_days, "planned_days": planned,
               "actual_median_days": act_days, "receipts": receipts}
    return rec, lt_info, drift


def assess_branch(branch: str, planner_an8: int | None = None) -> tuple[int, list[dict]]:
    """Size every planned item in the branch. Shared by find_param_gaps and the alert job.
    Returns (items_checked, per-item assessments)."""
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
    actuals = _actual_lead_times(branch)
    out = []
    for it in items:
        rec, lt_info, drift = _size(it, usage.get(it["itm"], []), actuals.get(it["itm"]))
        position = (float(it["on_hand"] or 0) - float(it["committed_qty"] or 0)
                    + float(it["on_po"] or 0))
        out.append({"item": it["litm"], "description": it["description"],
                    "planner_an8": it["planner_an8"],
                    "current": {k: it[k] for k in ("safety_stock", "reorder_point",
                                                   "reorder_qty", "lead_time_days")},
                    "suggested": rec.as_dict(), "lead_time": lt_info,
                    "inventory_position": round(position, 2),
                    "below_suggested_rop": bool(rec.reorder_point is not None
                                                and rec.avg_daily_usage > 0
                                                and position < rec.reorder_point),
                    "gaps": [{"code": g, "text": calc.GAP_TEXT[g]}
                             for g in calc.find_gaps(it, rec, drift)]})
    return len(items), out


@mcp.tool()
def find_param_gaps(branch: str, planner_an8: int | None = None, limit: int = 25) -> dict:
    """Find stocked items in a branch/plant whose safety stock, reorder point or lead time
    is missing or out of line with the last 13 weeks of usage. Ranked by average daily usage.
    Read-only: does not change JDE. Does not cover items outside the caller's scope.

    branch: JDE branch/plant (MCU), e.g. "30". planner_an8: optional planner filter.
    """
    limit = max(1, min(limit, 100))
    items_checked, assessed = assess_branch(branch, planner_an8)
    findings = [a for a in assessed if a["gaps"]]
    findings.sort(key=lambda f: f["suggested"]["avg_daily_usage"], reverse=True)
    # Suggested values on NO_LEAD_TIME items use the assumed default (see lead_time_assumed).
    counts: dict[str, int] = defaultdict(int)
    for f in findings:
        for g in f["gaps"]:
            counts[g["code"]] += 1
    return {"branch": branch, "items_checked": items_checked, "items_with_gaps": len(findings),
            "gap_counts": dict(counts), "top": _jsonable(findings[:limit])}


@mcp.tool()
def recommend_reorder(item: str, branch: str) -> dict:
    """Suggest safety stock, reorder point and reorder quantity for one item/branch from
    13 weeks of usage and a lead time taken from actual PO receipts (when there are enough),
    else the item-branch lead time, else an assumed default (lead_time.source says which). Shows the inputs so a planner can check
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
    rec, lt_info, drift = _size(it, weekly, _actual_lead_times(branch, it["itm"]).get(it["itm"]))
    available = float(it["on_hand"] or 0) - float(it["committed_qty"] or 0)
    position = available + float(it["on_po"] or 0)
    return {
        "item": it["litm"], "description": it["description"], "branch": branch,
        "current": _jsonable([{k: it[k] for k in ("safety_stock", "reorder_point", "reorder_qty",
                                                  "min_reorder_qty", "max_reorder_qty",
                                                  "order_multiple", "lead_time_days", "on_hand",
                                                  "committed_qty", "on_po")}])[0],
        "suggested": rec.as_dict(),
        "lead_time": lt_info,
        "inventory_position": round(position, 2),
        "below_suggested_rop": bool(rec.reorder_point is not None and position < rec.reorder_point),
        "gaps": [{"code": g, "text": calc.GAP_TEXT[g]} for g in calc.find_gaps(it, rec, drift)],
        "method": {"weeks": S.usage_weeks, "z": S.service_level_z,
                   "default_lead_time_days": S.default_lead_time_days,
                   "review_period_days": S.review_period_days, "weekly_usage": weekly},
    }


@mcp.tool()
def supplier_leadtime_drift(branch: str, min_receipts: int | None = None,
                            drift_pct: float | None = None, limit: int = 25) -> dict:
    """Compare actual supplier lead times (PO order date to receipt date, F4311/F43121) with the
    planned item-branch lead time (IBLTLV). Returns item/supplier pairs whose median differs by
    at least drift_pct, worst first, plus items with receipts but no planned lead time.
    Read-only: it does not change lead times in JDE.
    """
    limit = max(1, min(limit, 100))
    min_r = min_receipts if min_receipts is not None else S.min_lt_receipts
    pct = drift_pct if drift_pct is not None else S.lt_drift_pct
    rows = _jsonable(db.query(
        S, S.jde_user,
        "SELECT litm, supplier_an8, receipts, median_lt_days, p90_lt_days, min_lt_days, "
        "max_lt_days, planned_lt_days, last_receipt_date FROM JDEAGENT_OWN.V_SUPPLIER_LEADTIME "
        "WHERE mcu = :mcu AND receipts >= :r", {"mcu": branch.strip(), "r": min_r},
        max_rows=50_000))
    drifting, unplanned = [], []
    for r in rows:
        planned, med = float(r["planned_lt_days"] or 0), float(r["median_lt_days"] or 0)
        if planned <= 0:
            unplanned.append(r)
            continue
        delta = med - planned
        if abs(delta) / planned * 100 >= pct:
            drifting.append(r | {"delta_days": round(delta, 1),
                                 "delta_pct": round(delta / planned * 100, 1)})
    drifting.sort(key=lambda r: abs(r["delta_pct"]), reverse=True)
    unplanned.sort(key=lambda r: r["receipts"], reverse=True)
    return {"branch": branch, "pairs_checked": len(rows), "min_receipts": min_r,
            "drift_pct": pct, "drifting_count": len(drifting),
            "no_planned_lead_time_count": len(unplanned),
            "drifting": drifting[:limit], "no_planned_lead_time": unplanned[:limit]}


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
