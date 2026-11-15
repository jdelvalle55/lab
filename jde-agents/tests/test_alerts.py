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


def test_card_shape_and_cap(monkeypatch):
    monkeypatch.setattr(alerts, "CARD_MAX_ROWS", 2)
    rows = [{"id": str(i), "kind": "No planned lead time", "text": f"item {i}"} for i in range(5)]
    card = alerts.build_card("30", rows, [], ["x"], 5)
    att = card["attachments"][0]
    assert card["type"] == "message"
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    texts = [b.get("text", "") for b in att["content"]["body"]]
    assert sum(t.startswith("- item") for t in texts) == 2
    assert any(t.startswith("+3 more") for t in texts)
    import json
    assert len(json.dumps(card)) < 28_000


def test_deliver_posts_to_teams_when_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "OUTBOX_DIR", tmp_path)
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.invalid/hook")
    sent = {}
    monkeypatch.setattr(alerts, "post_teams", lambda url, card: sent.setdefault("url", url) and "posted to Teams (HTTP 202)")
    out = alerts.deliver("JDE Planner alerts: 1 new, branch 30", "body", True,
                         alerts.build_card("30", [], [], [], 0))
    assert sent["url"] == "https://example.invalid/hook" and out.startswith("posted to Teams")
    assert len(list(tmp_path.iterdir())) == 1   # outbox copy kept


def test_no_send_never_posts(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "OUTBOX_DIR", tmp_path)
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setattr(alerts, "post_teams", lambda *a: (_ for _ in ()).throw(AssertionError("posted")))
    assert alerts.deliver("s 30", "b", False, {}).startswith("dry run")
