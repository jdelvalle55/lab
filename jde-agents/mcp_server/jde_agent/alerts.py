"""Planner alert job. Runs the same checks as the MCP tools, compares them with the last run,
and sends only what is new or changed. Meant for Windows Task Scheduler (run_alerts.ps1).

  python -m jde_agent.alerts --branch 30            # dry run: writes to the outbox folder
  python -m jde_agent.alerts --branch 30 --send     # Teams if TEAMS_WEBHOOK_URL is set,
                                                    # else email via SMTP_*, else outbox

No LLM here: alert content is deterministic. State lives in a local JSON file per branch,
so nothing is written to the database.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from . import server

BASE = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("ALERT_STATE_DIR", BASE / "state"))
OUTBOX_DIR = Path(os.environ.get("ALERT_OUTBOX_DIR", BASE / "outbox"))
ALERT_MSG_TYPES = {t.strip().upper() for t in
                   os.environ.get("ALERT_MSG_TYPES", "B,C,D,E").split(",") if t.strip()}
MIN_DAYS_LATE = int(os.environ.get("ALERT_MIN_DAYS_LATE", "0"))


def collect(branch: str) -> dict[str, dict]:
    """Current alert conditions keyed by a stable id. fp = fingerprint of what matters."""
    alerts: dict[str, dict] = {}

    _, assessed = server.assess_branch(branch)
    for a in assessed:
        if a["below_suggested_rop"]:
            sug = a["suggested"]
            alerts[f"rop:{a['item']}"] = {
                "kind": "Below reorder point",
                "text": (f"{a['item']} {a['description'] or ''}: position "
                         f"{a['inventory_position']:g} < suggested ROP {sug['reorder_point']:g} "
                         f"(lead time {a['lead_time']['used_days']:g}d, {a['lead_time']['source']})"),
                "fp": f"{sug['reorder_point']}|{a['lead_time']['source']}"}

    mrp = server.list_mrp_exceptions(branch, min_days_late=MIN_DAYS_LATE, limit=200)
    for m in mrp["top"]:
        if (m["message_type"] or "").upper() in ALERT_MSG_TYPES:
            alerts[f"mrp:{m['message_id']}"] = {
                "kind": f"MRP {m['message_label']}",
                "text": (f"{m['litm']} {m['description'] or ''}: {m['message_label']} "
                         f"{m['order_type']} {m['order_no']} qty {m['qty']:g}, requested "
                         f"{m['requested_date']}, {m['days_late']} days late"),
                "fp": f"{m['qty']}|{m['requested_date']}"}

    lt = server.supplier_leadtime_drift(branch, limit=100)
    for r in lt["drifting"]:
        alerts[f"ltdrift:{r['litm']}:{r['supplier_an8']}"] = {
            "kind": "Lead time drift",
            "text": (f"{r['litm']} supplier {r['supplier_an8']}: actual median "
                     f"{r['median_lt_days']:g}d vs planned {r['planned_lt_days']:g}d "
                     f"({r['delta_pct']:+g}%, {r['receipts']} receipts)"),
            "fp": f"{r['median_lt_days']}"}
    for r in lt["no_planned_lead_time"]:
        alerts[f"ltmissing:{r['litm']}:{r['supplier_an8']}"] = {
            "kind": "No planned lead time",
            "text": (f"{r['litm']} supplier {r['supplier_an8']}: actual median "
                     f"{r['median_lt_days']:g}d (p90 {r['p90_lt_days']:g}d) from "
                     f"{r['receipts']} receipts; IBLTLV is 0"),
            "fp": f"{r['median_lt_days']}"}
    return alerts


def diff(prev: dict[str, str], cur: dict[str, dict]) -> tuple[list[dict], list[dict], list[str]]:
    new = [cur[k] | {"id": k} for k in cur if k not in prev]
    changed = [cur[k] | {"id": k} for k in cur if k in prev and prev[k] != cur[k]["fp"]]
    cleared = [k for k in prev if k not in cur]
    return new, changed, cleared


def render(branch: str, new: list[dict], changed: list[dict], cleared: list[str],
           total: int) -> str:
    lines = [f"JDE Planner alerts, branch {branch}, {datetime.now():%Y-%m-%d %H:%M}",
             f"{len(new)} new, {len(changed)} changed, {len(cleared)} cleared "
             f"({total} open in total).", ""]
    for title, rows in (("New", new), ("Changed", changed)):
        if rows:
            lines.append(f"{title}:")
            for kind in sorted({r["kind"] for r in rows}):
                lines.append(f"  {kind}")
                lines += [f"    - {r['text']}" for r in rows if r["kind"] == kind]
            lines.append("")
    if cleared:
        lines.append("Cleared:")
        lines += [f"  - {c}" for c in cleared]
        lines.append("")
    lines.append("Read-only report from the JDE Planner agent. Nothing was changed in JDE. "
                 "Values marked 'assumed' use a default lead time.")
    return "\n".join(lines)


CARD_MAX_ROWS = int(os.environ.get("TEAMS_MAX_ROWS", "40"))


def build_card(branch: str, new: list[dict], changed: list[dict], cleared: list[str],
               total: int) -> dict:
    """Adaptive Card for a Teams Workflows 'post to channel when a webhook request is received'
    flow. Kept well under the 28 KB card limit by capping rows."""
    body: list[dict] = [
        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": True,
         "text": f"JDE Planner alerts · branch {branch}"},
        {"type": "FactSet", "facts": [
            {"title": "New", "value": str(len(new))},
            {"title": "Changed", "value": str(len(changed))},
            {"title": "Cleared", "value": str(len(cleared))},
            {"title": "Open", "value": str(total)}]},
    ]
    shown = 0
    for title, rows in (("New", new), ("Changed", changed)):
        for kind in sorted({r["kind"] for r in rows}):
            body.append({"type": "TextBlock", "weight": "Bolder", "spacing": "Medium",
                         "wrap": True, "text": f"{title}: {kind}"})
            for r in [r for r in rows if r["kind"] == kind]:
                if shown >= CARD_MAX_ROWS:
                    break
                body.append({"type": "TextBlock", "wrap": True, "spacing": "None",
                             "text": f"- {r['text']}"})
                shown += 1
    hidden = len(new) + len(changed) - shown
    if hidden > 0:
        body.append({"type": "TextBlock", "wrap": True, "isSubtle": True,
                     "text": f"+{hidden} more; see the outbox file on the agent host."})
    if cleared:
        body.append({"type": "TextBlock", "wrap": True, "isSubtle": True, "spacing": "Medium",
                     "text": f"Cleared: {', '.join(cleared[:20])}"
                             + (" …" if len(cleared) > 20 else "")})
    body.append({"type": "TextBlock", "wrap": True, "isSubtle": True, "size": "Small",
                 "text": "Read-only report from the JDE Planner agent. Nothing was changed in "
                         "JDE. 'assumed' = default lead time."})
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard", "version": "1.4", "msteams": {"width": "Full"},
                    "body": body}}]}


def post_teams(url: str, card: dict) -> str:
    req = urllib.request.Request(url, data=json.dumps(card).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 202):
            raise RuntimeError(f"Teams webhook returned HTTP {resp.status}")
        return f"posted to Teams (HTTP {resp.status})"


def write_outbox(subject: str, body: str) -> str:
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTBOX_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{subject.split()[-1]}.txt"
    path.write_text(f"Subject: {subject}\n\n{body}\n", encoding="utf-8")
    return str(path)


def deliver(subject: str, body: str, send: bool, card: dict | None = None) -> str:
    """Always keeps a copy in the outbox; with send=True also posts to Teams (if
    TEAMS_WEBHOOK_URL is set) or emails (if SMTP_HOST and ALERT_TO are set)."""
    path = write_outbox(subject, body)
    teams = os.environ.get("TEAMS_WEBHOOK_URL", "").strip()
    if send and teams and card is not None:
        return post_teams(teams, card) + f"; copy in {path}"
    host = os.environ.get("SMTP_HOST")
    to = [a.strip() for a in os.environ.get("ALERT_TO", "").split(",") if a.strip()]
    if not send or not host or not to:
        return f"dry run: wrote {path}"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("SMTP_FROM", "jde-agent@localhost"), ", ".join(to)
    msg.set_content(body)
    port = int(os.environ.get("SMTP_PORT", "25"))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if os.environ.get("SMTP_STARTTLS", "false").lower() == "true":
            smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    return f"sent to {len(to)} recipient(s)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", required=True)
    ap.add_argument("--send", action="store_true", help="email instead of writing to outbox")
    ap.add_argument("--reset", action="store_true", help="forget previous state (re-alert all)")
    args = ap.parse_args(argv)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / f"alerts_{args.branch}.json"
    prev = {} if args.reset or not state_file.exists() else json.loads(state_file.read_text())
    cur = collect(args.branch)
    new, changed, cleared = diff(prev, cur)
    if new or changed or cleared:
        body = render(args.branch, new, changed, cleared, len(cur))
        result = deliver(f"JDE Planner alerts: {len(new)} new, branch {args.branch}", body,
                         args.send, build_card(args.branch, new, changed, cleared, len(cur)))
    else:
        result = "no changes, nothing sent"
    # Save state only after a successful delivery, so a failed send is retried next run.
    state_file.write_text(json.dumps({k: v["fp"] for k, v in cur.items()}, indent=1))
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} branch {args.branch}: {len(cur)} open, "
          f"{len(new)} new, {len(changed)} changed, {len(cleared)} cleared; {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
