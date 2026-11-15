"""Deterministic planning math. No database, no LLM: easy to test and to explain.

Formulas (all quantities in stocking UOM, time in days):
  avg_daily      = total usage over the window / (weeks * 7)
  sigma_daily    = stdev(weekly usage, missing weeks = 0) / sqrt(7)
  safety_stock   = z * sigma_daily * sqrt(lead_time)
  reorder_point  = avg_daily * lead_time + safety_stock
  reorder_qty    = avg_daily * review_period_days, then item-branch order rules:
                   raise to min (IBRQMN), round up to multiple (IBMULT), cap at max (IBRQMX)
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, asdict


@dataclass
class Recommendation:
    avg_daily_usage: float
    weekly_stdev: float
    lead_time_days: float
    safety_stock: float | None
    reorder_point: float | None
    reorder_qty: float | None
    lead_time_assumed: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def weekly_series(weekly_usage: list[float], weeks: int) -> list[float]:
    """Pad to `weeks` values; weeks with no issues count as zero usage."""
    vals = [max(float(v), 0.0) for v in weekly_usage][-weeks:]
    return vals + [0.0] * (weeks - len(vals))


def apply_order_rules(qty: float, min_qty: float = 0, max_qty: float = 0,
                      multiple: float = 0) -> float:
    """Apply JDE item-branch order rules the way a planner would expect: at least the minimum,
    a whole number of multiples, and never above the maximum (rounded down to a multiple)."""
    q = max(qty, min_qty or 0)
    if multiple and multiple > 0:
        q = math.ceil(q / multiple) * multiple
    if max_qty and max_qty > 0 and q > max_qty:
        q = math.floor(max_qty / multiple) * multiple if multiple and multiple > 0 else max_qty
    return float(q)


def recommend(weekly_usage: list[float], lead_time_days: float, *, weeks: int,
              z: float, review_period_days: int, min_qty: float = 0, max_qty: float = 0,
              multiple: float = 0, default_lead_time_days: float = 0) -> Recommendation:
    """default_lead_time_days > 0 is used when the item branch has no lead time; the result is
    then flagged lead_time_assumed so nobody mistakes it for a sized value."""
    assumed = False
    if (not lead_time_days or lead_time_days <= 0) and default_lead_time_days > 0:
        lead_time_days, assumed = float(default_lead_time_days), True
    series = weekly_series(weekly_usage, weeks)
    avg_daily = sum(series) / (weeks * 7)
    wk_sd = statistics.pstdev(series) if weeks > 1 else 0.0
    if not lead_time_days or lead_time_days <= 0:
        return Recommendation(avg_daily, wk_sd, 0.0, None, None, None, note=
                              "No lead time on the item branch (IBLTLV = 0); cannot size SS/ROP.")
    if avg_daily == 0:
        return Recommendation(0.0, 0.0, lead_time_days, 0.0, 0.0, 0.0, assumed,
                              f"No usage in the last {weeks} weeks.")
    sigma_daily = wk_sd / math.sqrt(7)
    ss = z * sigma_daily * math.sqrt(lead_time_days)
    rop = avg_daily * lead_time_days + ss
    roq = apply_order_rules(math.ceil(avg_daily * review_period_days), min_qty, max_qty, multiple)
    note = (f"ASSUMED lead time of {lead_time_days:g} days (item branch has none); "
            "set IBLTLV before using these values.") if assumed else ""
    return Recommendation(avg_daily, wk_sd, float(lead_time_days),
                          math.ceil(ss), math.ceil(rop), math.ceil(roq), assumed, note)


# Gap codes returned by find_param_gaps, most severe first.
GAP_TEXT = {
    "NO_LEAD_TIME": "Lead time is zero",
    "NO_ROP_WITH_USAGE": "Reorder point is zero but the item has usage",
    "ROP_BELOW_LT_DEMAND": "Reorder point is below demand over lead time",
    "NO_SAFETY_STOCK_WITH_USAGE": "Safety stock is zero but the item has usage",
    "ROP_EXCESSIVE": "Reorder point is more than 3x the suggested value",
    "PARAMS_WITHOUT_USAGE": "Has ROP/safety stock but no usage in the window",
    "LEAD_TIME_DRIFT": "Actual supplier lead time differs from the planned lead time",
}


def choose_lead_time(planned_days: float, actual_days: float | None, receipts: int,
                     min_receipts: int) -> tuple[float, str]:
    """Pick the lead time used for sizing: actual receipts history first (when there is enough
    of it), then the item-branch lead time, else none (the caller may apply an assumed default)."""
    if actual_days is not None and receipts >= min_receipts and actual_days > 0:
        return float(actual_days), "actual"
    if planned_days and planned_days > 0:
        return float(planned_days), "item_branch"
    return 0.0, "none"


def lead_time_drift(planned_days: float, actual_days: float | None, receipts: int,
                    min_receipts: int, drift_pct: float) -> bool:
    if actual_days is None or receipts < min_receipts or not planned_days or planned_days <= 0:
        return False
    return abs(actual_days - planned_days) / planned_days * 100 >= drift_pct


def find_gaps(current: dict, rec: Recommendation, drift: bool = False) -> list[str]:
    """current: safety_stock, reorder_point, lead_time_days from V_ITEM_PLANNING.
    drift: result of lead_time_drift() for this item branch."""
    gaps: list[str] = []
    ss, rop = float(current.get("safety_stock") or 0), float(current.get("reorder_point") or 0)
    has_usage = rec.avg_daily_usage > 0
    if not current.get("lead_time_days"):
        gaps.append("NO_LEAD_TIME")
    if has_usage and rop == 0:
        gaps.append("NO_ROP_WITH_USAGE")
    elif has_usage and rec.lead_time_days and rop < rec.avg_daily_usage * rec.lead_time_days:
        gaps.append("ROP_BELOW_LT_DEMAND")
    if has_usage and ss == 0:
        gaps.append("NO_SAFETY_STOCK_WITH_USAGE")
    if rec.reorder_point and rop > 3 * rec.reorder_point:
        gaps.append("ROP_EXCESSIVE")
    if not has_usage and (rop > 0 or ss > 0):
        gaps.append("PARAMS_WITHOUT_USAGE")
    if drift:
        gaps.append("LEAD_TIME_DRIFT")
    return gaps
