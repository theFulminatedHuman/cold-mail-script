#!/usr/bin/env python3
"""Send the cold emails you approved, from your Gmail, with your resume attached.

Drafts live in outbox/<company>.md (Claude Code writes them, see CLAUDE.md):

    to: priya@examplelabs.in
    company: Example Labs
    subject: backend work at examplelabs
    status: approved
    ---
    Hi Priya,
    ...

Only drafts with `status: approved` are sent. After sending, the file is marked
`status: sent` and logged to sent_log.csv, so nothing goes out twice.

Usage:
  python send.py                    # show each approved email, ask y/n before sending
  python send.py --list             # list drafts and their status, send nothing
  python send.py outbox/foo.md      # only these files (still asks y/n)
  python send.py --yes outbox/foo.md   # no prompt: for files you already approved in chat
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import smtplib
import sys
from pathlib import Path

from outreach import EMAIL_RE, HERE, GmailSender, build_message, load_config, load_sent, log_sent

OUTBOX = HERE / "outbox"
HEADER_KEYS = ("to", "company", "subject", "status")


def parse_draft(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    head, sep, body = text.partition("\n---\n")
    if not sep:
        raise ValueError("missing the '---' line between the header and the body")
    draft = {"path": path, "body": body.strip()}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() in HEADER_KEYS:
            draft[key.strip().lower()] = value.strip()
    for key in HEADER_KEYS:
        if not draft.get(key):
            raise ValueError(f"header is missing '{key}:'")
    if not EMAIL_RE.match(draft["to"]):
        raise ValueError(f"'{draft['to']}' is not a valid email address")
    if not draft["body"]:
        raise ValueError("body is empty")
    return draft


def set_status(path: Path, status: str) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    for i, line in enumerate(lines):
        if line.strip() == "---":
            break
        if line.lower().startswith("status:"):
            lines[i] = f"status: {status}"
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="draft files to send (default: every approved draft in outbox/)")
    ap.add_argument("--yes", action="store_true", help="send without asking (only for drafts already approved)")
    ap.add_argument("--list", action="store_true", help="list drafts and their status, send nothing")
    args = ap.parse_args()

    paths = [Path(f) for f in args.files] if args.files else sorted(OUTBOX.glob("*.md"))
    drafts = []
    for p in paths:
        try:
            drafts.append(parse_draft(p))
        except (OSError, ValueError) as e:
            print(f"skipping {p}: {e}")

    if args.list:
        for d in drafts:
            print(f"{d['status']:<9} {d['company']:<30} {d['to']:<35} {d['path'].name}")
        return

    cfg = load_config()
    sent = load_sent()
    sent_emails = {r["email"].strip().lower() for r in sent}
    today = dt.date.today().isoformat()
    sent_today = sum(1 for r in sent if r["sent_at"].startswith(today))

    todo = []
    for d in drafts:
        if d["status"].lower() != "approved":
            continue
        if d["to"].lower() in sent_emails:
            print(f"skipping {d['company']}: already emailed {d['to']} (see sent_log.csv)")
            set_status(d["path"], "sent")
            continue
        todo.append(d)

    if not todo:
        print("No approved drafts to send. Mark a draft 'status: approved' once you're happy with it.")
        return

    attach_name = cfg.get("resume_attachment_name") or Path(cfg["resume_path"]).name
    print(f"Sending from {cfg['your_email']}. {len(todo)} approved, sent today: {sent_today}/{cfg['daily_limit']}.")
    if args.yes and not os.environ.get("GMAIL_APP_PASSWORD"):
        sys.exit("--yes needs GMAIL_APP_PASSWORD set in the environment (there's nobody to type it).")
    sender = GmailSender(cfg)

    for d in todo:
        if sent_today >= cfg["daily_limit"]:
            print(f"Daily limit of {cfg['daily_limit']} reached. The rest stay approved for tomorrow.")
            break

        print(f"\n{'=' * 72}\nTo:      {d['to']}  ({d['company']})\nSubject: {d['subject']}\nAttach:  {attach_name}")
        print(f"{'-' * 72}\n{d['body']}\n{'=' * 72}")

        if not args.yes:
            choice = input("[y] send  [s] skip  [q] quit > ").strip().lower()
            if choice == "q":
                print("Stopped.")
                return
            if choice != "y":
                print("  skipped (still approved).")
                continue

        company = {"company": d["company"], "email": d["to"]}
        try:
            sender.send(build_message(cfg, company, d["subject"], d["body"]))
        except smtplib.SMTPAuthenticationError:
            sys.exit("Gmail rejected the login. Check your_email in config.json and GMAIL_APP_PASSWORD.")
        except (smtplib.SMTPException, OSError) as e:
            print(f"  send failed: {e}")
            continue
        log_sent(company, d["subject"])
        set_status(d["path"], "sent")
        sent_today += 1
        print(f"  sent. ({sent_today}/{cfg['daily_limit']} today)")

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Only the emails marked 'sent.' above went out.")
