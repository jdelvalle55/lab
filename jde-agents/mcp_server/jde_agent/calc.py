"""Deterministic planning math. No database, no LLM: easy to test and to explain.

Formulas (all quantities in stocking UOM, time in days):
  avg_daily      = total usage over the window / (weeks * 7)
  sigma_daily    = stdev(weekly usage, missing weeks = 0) / sqrt(7)
  safety_stock   = z * sigma_daily * sqrt(lead_time)
  reorder_point  = avg_daily * lead_time + safety_stock
  reorder_qty    = avg_daily * review_period_days
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
    note: str = ""

    def as_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def weekly_series(weekly_usage: list[float], weeks: int) -> list[float]:
    """Pad to `weeks` values; weeks with no issues count as zero usage."""
    vals = [max(float(v), 0.0) for v in weekly_usage][-weeks:]
    return vals + [0.0] * (weeks - len(vals))


def recommend(weekly_usage: list[float], lead_time_days: float, *, weeks: int,
              z: float, review_period_days: int) -> Recommendation:
    series = weekly_series(weekly_usage, weeks)
    avg_daily = sum(series) / (weeks * 7)
    wk_sd = statistics.pstdev(series) if weeks > 1 else 0.0
    if not lead_time_days or lead_time_days <= 0:
        return Recommendation(avg_daily, wk_sd, 0.0, None, None, None,
                              "No lead time on the item branch (IBLTLV = 0); cannot size SS/ROP.")
    if avg_daily == 0:
        return Recommendation(0.0, 0.0, lead_time_days, 0.0, 0.0, 0.0,
                              f"No usage in the last {weeks} weeks.")
    sigma_daily = wk_sd / math.sqrt(7)
    ss = z * sigma_daily * math.sqrt(lead_time_days)
    rop = avg_daily * lead_time_days + ss
    roq = avg_daily * review_period_days
    return Recommendation(avg_daily, wk_sd, float(lead_time_days),
                          math.ceil(ss), math.ceil(rop), math.ceil(roq))


# Gap codes returned by find_param_gaps, most severe first.
GAP_TEXT = {
    "NO_LEAD_TIME": "Lead time is zero",
    "NO_ROP_WITH_USAGE": "Reorder point is zero but the item has usage",
    "ROP_BELOW_LT_DEMAND": "Reorder point is below demand over lead time",
    "NO_SAFETY_STOCK_WITH_USAGE": "Safety stock is zero but the item has usage",
    "ROP_EXCESSIVE": "Reorder point is more than 3x the suggested value",
    "PARAMS_WITHOUT_USAGE": "Has ROP/safety stock but no usage in the window",
}


def find_gaps(current: dict, rec: Recommendation) -> list[str]:
    """current: safety_stock, reorder_point, lead_time_days from V_ITEM_PLANNING."""
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
    return gaps
