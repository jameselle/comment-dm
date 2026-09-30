"""State on disk (SQLite): which comments were handled, where each person is in the flow, what was sent.
It is what makes "never reply twice" hold across restarts."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

SCHEMA = """
create table if not exists comments (
  id text primary key, media_id text, user_id text, username text, text text,
  commented_at real, handled_at real, outcome text
);
create table if not exists contacts (
  igsid text primary key, username text, campaign text, stage text,
  prompts integer default 0, last_inbound_at real, updated_at real, delivered_at real
);
create table if not exists seen_messages (id text primary key, at real);
create table if not exists sent (at real, kind text, target text, campaign text, dry_run integer);
create table if not exists kv (key text primary key, value text);
"""


class Store:
    def __init__(self, path: Path, now: Callable[[], float] = time.time):
        self.now = now  # the Engine hands in its clock, so every timestamp agrees with it
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- key/value (the start time, cursors)
    def get(self, key: str) -> Optional[str]:
        row = self.db.execute("select value from kv where key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def put(self, key: str, value: str) -> None:
        self.db.execute("insert into kv(key, value) values(?, ?) on conflict(key) do update set value = excluded.value", (key, value))
        self.db.commit()

    # ---- comments
    def comment_handled(self, comment_id: str) -> bool:
        return self.db.execute("select 1 from comments where id = ?", (comment_id,)).fetchone() is not None

    def record_comment(self, c: Dict[str, Any], outcome: str) -> None:
        self.db.execute(
            "insert or ignore into comments(id, media_id, user_id, username, text, commented_at, handled_at, outcome) values(?,?,?,?,?,?,?,?)",
            (c["id"], c.get("media_id"), c.get("user_id"), c.get("username"), c.get("text"), c.get("at"), self.now(), outcome),
        )
        self.db.commit()

    # ---- contacts (people in a flow)
    def contact(self, igsid: str) -> Optional[sqlite3.Row]:
        return self.db.execute("select * from contacts where igsid = ?", (igsid,)).fetchone()

    def upsert_contact(self, igsid: str, **fields: Any) -> None:
        fields["updated_at"] = self.now()
        existing = self.contact(igsid)
        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"update contacts set {sets} where igsid = ?", (*fields.values(), igsid))
        else:
            cols = ["igsid", *fields.keys()]
            self.db.execute(f"insert into contacts({', '.join(cols)}) values({', '.join('?' for _ in cols)})", (igsid, *fields.values()))
        self.db.commit()

    # ---- inbound messages already processed
    def message_seen(self, mid: str) -> bool:
        return self.db.execute("select 1 from seen_messages where id = ?", (mid,)).fetchone() is not None

    def mark_seen(self, mid: str) -> None:
        self.db.execute("insert or ignore into seen_messages(id, at) values(?, ?)", (mid, self.now()))
        self.db.commit()

    # ---- what went out, for the hourly cap and the status report
    def log_sent(self, kind: str, target: str, campaign: str, dry_run: bool, at: Optional[float] = None) -> None:
        self.db.execute("insert into sent(at, kind, target, campaign, dry_run) values(?,?,?,?,?)", (at or self.now(), kind, target, campaign, int(dry_run)))
        self.db.commit()

    def sent_last_hour(self, dry_run: bool, now: Optional[float] = None) -> int:
        return self.db.execute("select count(*) from sent where at > ? and dry_run = ?", ((now or self.now()) - 3600, int(dry_run))).fetchone()[0]

    # ---- retention (what docs/privacy.html promises)
    def prune(self, before: float) -> int:
        """Delete records older than `before` (epoch). Returns how many rows went."""
        n = 0
        for sql in ("delete from comments where handled_at < ?", "delete from seen_messages where at < ?",
                    "delete from sent where at < ?", "delete from contacts where updated_at < ?"):
            n += self.db.execute(sql, (before,)).rowcount
        self.db.commit()
        return n

    def forget(self, username: str) -> int:
        """A deletion request: remove everything held about one person."""
        u = username.lstrip("@").lower()
        ids = [r["igsid"] for r in self.db.execute("select igsid from contacts where lower(username) = ?", (u,))]
        n = self.db.execute("delete from comments where lower(username) = ?", (u,)).rowcount
        for igsid in ids:
            n += self.db.execute("delete from sent where target = ?", (igsid,)).rowcount
        n += self.db.execute("delete from contacts where lower(username) = ?", (u,)).rowcount
        self.db.commit()
        return n

    def stats(self) -> Dict[str, Any]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "comments handled": q("select count(*) from comments"),
            "people in a flow": q("select count(*) from contacts"),
            "links delivered": q("select count(*) from contacts where delivered_at is not null"),
            "waiting on a follow": q("select count(*) from contacts where stage = 'gated'"),
            "sent (live)": q("select count(*) from sent where dry_run = 0"),
            "sent (dry run)": q("select count(*) from sent where dry_run = 1"),
            "follow check": self.get("follow_check") or "not tried yet",
            "buttons on first DM": self.get("private_reply_buttons") or "not tried yet",
            "link buttons": self.get("link_buttons") or "not tried yet",
        }
