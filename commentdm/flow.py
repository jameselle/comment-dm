"""The flow, as ManyChat runs it, within Instagram's rules:

  comment with the keyword ─► public reply to the comment
                           └► private reply: ONE plain-text DM (no buttons allowed) asking them to reply
  they reply (consent) ────► do they follow?  yes ─► send the link(s)                 [delivered]
                                              no  ─► "follow, then tap"  + ✅ button   [gated]
  they tap / reply again ──► follow check again, up to max_prompts times, then stop   [gave_up]

Instagram's windows: a private reply within 7 days of the comment; anything else within 24 hours of
their last message. Someone who DMs the keyword directly (no comment) enters at "they reply".
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .graph import GraphError
from .store import Store

DAY = 86400
FOLLOWED_PAYLOAD = "COMMENTDM_FOLLOWED"


def parse_time(s: Optional[str]) -> float:
    if not s:
        return 0.0
    return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").timestamp()


def keyword_in(keyword: str, text: Optional[str]) -> bool:
    """Whole word, any case: 'clip!' and 'Clip please' match, 'eclipse' doesn't."""
    return bool(text) and re.search(rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


class Engine:
    def __init__(self, graph: Any, store: Store, config: Dict[str, Any], dry_run: bool = True,
                 now: Callable[[], float] = time.time, log: Callable[[str], None] = print):
        self.g, self.s, self.cfg, self.dry_run, self.now, self.log = graph, store, config, dry_run, now, log
        self.s.now = now
        self.safety = config.get("safety", {})
        self.campaigns: List[Dict[str, Any]] = config["campaigns"]
        self._me: Optional[str] = None

    # ------------------------------------------------------------------ helpers
    @property
    def me(self) -> str:
        if self._me is None:
            self._me = self.s.get("me_id") or str(self.g.me().get("user_id"))
            self.s.put("me_id", self._me)
        return self._me

    def started_at(self) -> float:
        """Nothing from before the first run is answered: switching this on never floods old comments."""
        got = self.s.get("started_at")
        if got is None:
            got = str(self.now())
            self.s.put("started_at", got)
        return float(got)

    def campaign_for(self, text: Optional[str], media_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        for c in self.campaigns:
            media = c.get("media", "all")
            if media_id and media != "all" and media_id not in media:
                continue
            if keyword_in(c["keyword"], text):
                return c
        return None

    def allowed(self, username: Optional[str]) -> bool:
        allow = [u.lstrip("@").lower() for u in self.safety.get("allowlist", [])]
        return not allow or (username or "").lstrip("@").lower() in allow

    def under_cap(self) -> bool:
        return self.s.sent_last_hour(self.dry_run, self.now()) < int(self.safety.get("max_dms_per_hour", 60))

    def _send(self, kind: str, target: str, campaign: Dict[str, Any], call: Callable[[], Dict[str, Any]], describe: str) -> Dict[str, Any]:
        """Every outgoing call goes through here: dry run logs instead of sending, and everything is counted."""
        self.s.log_sent(kind, target, campaign["name"], self.dry_run, self.now())
        if self.dry_run:
            self.log(f"[dry run] {kind} → {describe}")
            return {"recipient_id": f"dry:{target}"}
        got = call()
        self.log(f"{kind} → {describe}")
        return got

    # ------------------------------------------------------------------ comments
    def on_comment(self, c: Dict[str, Any]) -> str:
        """c: id, text, at (epoch), user_id, username, media_id. Returns what happened (also stored)."""
        if self.s.comment_handled(c["id"]):
            return "already handled"
        outcome = self._decide_comment(c)
        if outcome != "capped":  # a capped comment is retried next round
            self.s.record_comment(c, outcome)
        return outcome

    def _decide_comment(self, c: Dict[str, Any]) -> str:
        if c.get("user_id") == self.me:
            return "own comment"
        if c.get("at", 0) < self.started_at():
            return "before start"
        camp = self.campaign_for(c.get("text"), c.get("media_id"))
        if not camp:
            return "no keyword"
        if not self.allowed(c.get("username")):
            return "not on the allowlist"
        if self.now() - c.get("at", 0) > 7 * DAY:
            return "too old for a private reply"
        if not self.under_cap():
            self.log("hourly cap reached: holding comments until next round")
            return "capped"
        who = f"@{c.get('username') or c.get('user_id')}"
        try:
            if camp.get("public_replies"):
                reply = random.choice(camp["public_replies"])
                self._send("public reply", c["id"], camp, lambda: self.g.reply_public(c["id"], reply), f"{who}: {reply}")
            got = self._send("private reply", c["id"], camp, lambda: self.g.private_reply(c["id"], camp["private_reply"]), f"{who}: {camp['private_reply']}")
        except GraphError as e:
            self.log(f"couldn't reply to {who}'s comment: {e}")
            return f"error {e.code or e.status}"
        igsid = str(got.get("recipient_id") or f"comment:{c['id']}")
        if not self.s.contact(igsid):
            self.s.upsert_contact(igsid, username=c.get("username"), campaign=camp["name"], stage="awaiting_reply", prompts=0)
        return "replied"

    # ------------------------------------------------------------------ messages
    def on_message(self, m: Dict[str, Any]) -> str:
        """m: id, from_id, username, text, at. An inbound DM (not ours)."""
        if self.s.message_seen(m["id"]):
            return "already seen"
        self.s.mark_seen(m["id"])
        if m["from_id"] == self.me or m.get("at", 0) < self.started_at():
            return "ignored"
        igsid = m["from_id"]
        contact = self.s.contact(igsid)
        if contact is None:
            camp = self.campaign_for(m.get("text"))
            if not camp or not self.allowed(m.get("username")):
                return "not a keyword DM"  # an ordinary DM: leave it for a human
            self.s.upsert_contact(igsid, username=m.get("username"), campaign=camp["name"], stage="awaiting_reply", prompts=0)
            contact = self.s.contact(igsid)
        if contact["stage"] in ("delivered", "gave_up"):
            return f"already {contact['stage']}"
        camp = next((c for c in self.campaigns if c["name"] == contact["campaign"]), None)
        if camp is None:
            return "campaign removed"
        self.s.upsert_contact(igsid, last_inbound_at=m.get("at") or self.now())
        if not self.under_cap():
            self.log("hourly cap reached: will answer when the next message arrives")
            return "capped"
        return self._gate_or_deliver(igsid, contact, camp)

    def _gate_or_deliver(self, igsid: str, contact: Any, camp: Dict[str, Any]) -> str:
        who = f"@{contact['username'] or igsid}"
        try:
            follows = True
            if camp.get("follow_gate", True):
                follows = bool(self.g.profile(igsid).get("is_user_follow_business"))
            if follows:
                self._send("link", igsid, camp, lambda: self.g.send(igsid, camp["deliver"]), f"{who}: {camp['deliver'][:60]}…")
                self.s.upsert_contact(igsid, stage="delivered", delivered_at=self.now())
                return "delivered"
            prompts = int(contact["prompts"] or 0)
            if prompts >= int(camp.get("max_prompts", 3)):
                self.s.upsert_contact(igsid, stage="gave_up")
                return "gave up (never followed)"
            text = camp["follow_prompt"] if prompts == 0 else camp.get("still_not_following", camp["follow_prompt"])
            button = [{"title": camp.get("follow_button", "I followed ✅"), "payload": FOLLOWED_PAYLOAD}]
            self._send("follow prompt", igsid, camp, lambda: self.g.send(igsid, text, button), f"{who}: {text}")
            self.s.upsert_contact(igsid, stage="gated", prompts=prompts + 1)
            return "asked to follow"
        except GraphError as e:
            self.log(f"couldn't message {who}: {e}")
            return f"error {e.code or e.status}"

    # ------------------------------------------------------------------ polling (no webhooks needed)
    def poll_once(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        bump = lambda k: counts.__setitem__(k, counts.get(k, 0) + 1)  # noqa: E731
        self.started_at()
        self.s.prune(self.now() - float(self.safety.get("keep_days", 90)) * DAY)
        days = float(self.safety.get("media_days", 7))
        for media in self.g.recent_media(limit=int(self.safety.get("media_limit", 10))):
            if self.now() - parse_time(media.get("timestamp")) > days * DAY:
                continue
            for c in self.g.comments(media["id"]):
                frm = c.get("from") or {}
                bump(self.on_comment({"id": c["id"], "text": c.get("text"), "at": parse_time(c.get("timestamp")),
                                      "user_id": str(frm.get("id") or ""), "username": frm.get("username") or c.get("username"),
                                      "media_id": media["id"]}))
        for conv in self.g.conversations():
            try:
                msgs = self.g.messages(conv["id"])
            except GraphError as e:  # one unreadable conversation mustn't stop the round
                self.log(f"couldn't read a conversation: {e}")
                bump("unreadable conversation")
                continue
            for msg in reversed(msgs):  # the API lists newest first
                frm = msg.get("from") or {}
                bump(self.on_message({"id": msg["id"], "from_id": str(frm.get("id") or ""), "username": frm.get("username"),
                                      "text": msg.get("message"), "at": parse_time(msg.get("created_time"))}))
        return counts
