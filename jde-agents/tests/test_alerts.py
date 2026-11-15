import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
import os  # noqa: E402

os.environ.setdefault("ORACLE_DSN", "x")
os.environ.setdefault("ORACLE_PASSWORD", "x")
os.environ.setdefault("JDE_AGENT_USER", "JDE")
from jde_agent import alerts  # noqa: E402


def test_diff_new_changed_cleared():
    prev = {"a": "1", "b": "1", "c": "1"}
    cur = {"a": {"fp": "1", "kind": "k", "text": "t"},
           "b": {"fp": "2", "kind": "k", "text": "t"},
           "d": {"fp": "1", "kind": "k", "text": "t"}}
    new, changed, cleared = alerts.diff(prev, cur)
    assert [n["id"] for n in new] == ["d"]
    assert [c["id"] for c in changed] == ["b"]
    assert cleared == ["c"]


def test_render_groups_by_kind():
    body = alerts.render("30", [{"id": "x", "kind": "Below reorder point", "text": "item A"}],
                         [], ["mrp:1"], 3)
    assert "1 new, 0 changed, 1 cleared (3 open in total)" in body
    assert "Below reorder point" in body and "item A" in body and "mrp:1" in body


def test_deliver_dry_run_writes_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "OUTBOX_DIR", tmp_path)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    result = alerts.deliver("JDE Planner alerts: 1 new, branch 30", "body", send=True)
    assert result.startswith("dry run") and len(list(tmp_path.iterdir())) == 1
