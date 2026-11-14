import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
from jde_agent import calc  # noqa: E402

KW = dict(weeks=13, z=1.65, review_period_days=30)


def test_flat_usage_has_no_safety_stock():
    rec = calc.recommend([70.0] * 13, 14, **KW)          # 10/day, no variability
    assert rec.avg_daily_usage == 10
    assert rec.safety_stock == 0
    assert rec.reorder_point == 140                      # 10/day * 14 days
    assert rec.reorder_qty == 300                        # 10/day * 30 days


def test_missing_weeks_count_as_zero_and_add_variability():
    rec = calc.recommend([140.0] * 6, 14, **KW)          # 6 weeks of usage, 7 weeks of none
    assert math.isclose(rec.avg_daily_usage, 840 / 91)
    assert rec.safety_stock > 0
    assert rec.reorder_point >= rec.safety_stock


def test_zero_lead_time_returns_no_sizing():
    rec = calc.recommend([70.0] * 13, 0, **KW)
    assert rec.reorder_point is None and "lead time" in rec.note


def test_gaps_detected():
    rec = calc.recommend([70.0] * 13, 14, **KW)
    current = {"safety_stock": 0, "reorder_point": 50, "lead_time_days": 14}
    assert calc.find_gaps(current, rec) == ["ROP_BELOW_LT_DEMAND", "NO_SAFETY_STOCK_WITH_USAGE"]


def test_params_without_usage():
    rec = calc.recommend([], 14, **KW)
    assert calc.find_gaps({"safety_stock": 5, "reorder_point": 10, "lead_time_days": 14}, rec) == [
        "PARAMS_WITHOUT_USAGE"]


def test_excessive_rop():
    rec = calc.recommend([70.0] * 13, 14, **KW)
    assert "ROP_EXCESSIVE" in calc.find_gaps(
        {"safety_stock": 10, "reorder_point": 1000, "lead_time_days": 14}, rec)
