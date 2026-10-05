"""The flow, as ManyChat runs it, within Instagram's rules:

  comment with the keyword ─► public reply to the comment
                           └► private reply: ONE plain-text DM (no buttons allowed) asking them to reply
  they reply (consent) ────► do they follow?  yes ─► send the link(s)                 [delivered]
                                              no  ─► "follow, then tap"  + ✅ button   [gated]
  they tap / reply again ──► follow check again, up to max_prompts times, then stop   [gave_up]
  no tap after a few hours ► one nudge: followed by now? the link. Not yet? one reminder [nudge_after_minutes]

Instagram's windows: a private reply within 7 days of the comment; anything else within 24 hours of
their last message. Someone who DMs the keyword directly (no comment) enters at "they reply".

Each person has their own place in each campaign, so yesterday's untapped button still works after they comment
today's keyword. A reply can't say which button it came from (polling reads only its text), so one reply answers
every campaign they're waiting on.
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .graph import GraphError
from .store import Store

DAY = 86400
NUDGE_AFTER_MINUTES = 180  # default; a campaign's nudge_after_minutes overrides it, 0 turns nudges off
NUDGE_MARGIN = 3600        # never nudge in the last hour of someone's 24-hour window
FOLLOWED_PAYLOAD = "COMMENTDM_FOLLOWED"
SEND_PAYLOAD = "COMMENTDM_SEND"


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
        # Someone commenting again: if they already have the links, a "tap and I'll send them" DM would promise
        # something the tap can't deliver (delivered contacts are left alone), so the private reply IS the links.
        prior = self.s.contact_by_username(c["username"], camp["name"]) if c.get("username") else None
        known = self.s.contact_by_username(c["username"]) if c.get("username") else None
        try:
            if camp.get("public_replies"):
                reply = random.choice(camp["public_replies"])
                self._send("public reply", c["id"], camp, lambda: self.g.reply_public(c["id"], reply), f"{who}: {reply}")
            if prior is not None and prior["stage"] == "delivered":
                text = camp.get("deliver_plain") or camp["deliver"]
                self._send("private reply", c["id"], camp, lambda: self.g.private_reply(c["id"], text), f"{who}: {text[:60]}…")
                return "replied (links again)"
            got = self._private_reply(c, camp, who)
        except GraphError as e:
            self.log(f"couldn't reply to {who}'s comment: {e}")
            return f"error {e.code or e.status}"
        igsid = prior["igsid"] if prior is not None else str(got.get("recipient_id") or (known["igsid"] if known else f"comment:{c['id']}"))
        if prior is None or prior["stage"] == "gave_up":
            # New to this campaign (whatever other campaigns they're in), or a fresh try after giving up.
            self.s.upsert_contact(igsid, camp["name"], username=c.get("username"), stage="awaiting_reply", prompts=0)
        return "replied"

    def _private_reply(self, c: Dict[str, Any], camp: Dict[str, Any], who: str) -> Dict[str, Any]:
        """With a [Send me the tools!] button if the campaign has one and Instagram hasn't refused buttons on
        private replies before; otherwise the plain-text version that asks them to reply."""
        label = camp.get("private_reply_button")
        if label and self.s.get("private_reply_buttons") != "refused":
            button = [{"title": label, "payload": SEND_PAYLOAD}]
            try:
                got = self._send("private reply", c["id"], camp, lambda: self.g.private_reply(c["id"], camp["private_reply"], button),
                                 f"{who}: {camp['private_reply']} [{label}]")
                self.s.put("private_reply_buttons", "ok")
                return got
            except GraphError as e:
                if e.subcode == 2534022 or e.code == 10 and "window" in str(e):
                    raise  # outside the 7-day window: plain text won't help
                self.s.put("private_reply_buttons", "refused")
                self.log(f"Instagram refused a button on the private reply ({e}); using plain text from now on")
        text = camp.get("private_reply_plain") if label else camp["private_reply"]
        return self._send("private reply", c["id"], camp, lambda: self.g.private_reply(c["id"], text), f"{who}: {text}")

    def _deliver(self, igsid: str, camp: Dict[str, Any], who: str) -> None:
        """The links: as tap-to-open buttons when the campaign lists them, else (or if refused) as plain text."""
        buttons = camp.get("deliver_buttons")
        if buttons and self.s.get("link_buttons") != "refused":
            try:
                self._send("link", igsid, camp, lambda: self.g.send_link_buttons(igsid, camp["deliver"], buttons),
                           f"{who}: {camp['deliver'][:50]}… [{' | '.join(b['title'] for b in buttons)}]")
                self.s.put("link_buttons", "ok")
                return
            except GraphError as e:
                self.s.put("link_buttons", "refused")
                self.log(f"Instagram refused link buttons ({e}); sending the links as text from now on")
        text = camp.get("deliver_plain") if buttons else camp["deliver"]
        self._send("link", igsid, camp, lambda: self.g.send(igsid, text), f"{who}: {text[:60]}…")

    # ------------------------------------------------------------------ messages
    def on_message(self, m: Dict[str, Any]) -> str:
        """m: id, from_id, username, text, at. An inbound DM (not ours)."""
        if self.s.message_seen(m["id"]):
            return "already seen"
        self.s.mark_seen(m["id"])
        if m["from_id"] == self.me or m.get("at", 0) < self.started_at():
            return "ignored"
        if self.now() - m.get("at", 0) > DAY:
            return "too old to answer"  # Instagram only allows replies within 24 hours of their message
        igsid = m["from_id"]
        rows = self.s.contacts(igsid)
        if rows:
            self.s.touch_inbound(igsid, m.get("at") or self.now())  # first, so a reply nothing answers shows as unanswered
        # Instagram copies the comment into the new DM thread, dated when it was commented (before our DM). It isn't
        # a reply: a campaign still awaiting one only counts messages that came after we asked.
        waiting = [r for r in rows if r["stage"] == "gated" or r["stage"] == "awaiting_reply" and m.get("at", 0) > (r["updated_at"] or 0)]
        camp = self.campaign_for(m.get("text"))
        if camp and self.allowed(m.get("username")) and not any(r["campaign"] == camp["name"] for r in rows):
            # The keyword sent as a DM (no comment): it starts that campaign, whatever else they're in.
            self.s.upsert_contact(igsid, camp["name"], username=m.get("username"), stage="awaiting_reply", prompts=0)
            waiting.append(self.s.contact(igsid, camp["name"]))
        if not waiting:
            if not rows:
                return "not a keyword DM"  # an ordinary DM: leave it for a human
            if any(r["stage"] == "awaiting_reply" for r in rows):
                return "waiting for their reply"
            return f"already {rows[-1]['stage']}"
        by_name = {c["name"]: c for c in self.campaigns}
        pairs = [(r, by_name[r["campaign"]]) for r in waiting if r["campaign"] in by_name]
        if not pairs:
            return "campaign removed"
        self.s.touch_inbound(igsid, m.get("at") or self.now())  # a keyword DM's new row too
        if not self.under_cap():
            self.log("hourly cap reached: will answer when the next message arrives")
            return "capped"
        return self._gate_or_deliver(igsid, pairs)

    def _gate_or_deliver(self, igsid: str, pairs: List[Tuple[Any, Dict[str, Any]]]) -> str:
        """pairs: (their row, its campaign), oldest first. One follow check, every link they're owed, and at most
        one follow prompt however many campaigns are waiting on a follow."""
        who = f"@{pairs[-1][0]['username'] or igsid}"
        delivered, ask, gave_up = 0, [], 0
        try:
            follows: Optional[bool] = True
            if any(camp.get("follow_gate", True) for _, camp in pairs):
                try:
                    follows = bool(self.g.profile(igsid).get("is_user_follow_business"))
                    self.s.put("follow_check", "ok")
                except GraphError as e:
                    if e.code != 230:
                        raise
                    # They've replied, yet Instagram still won't show whether they follow (it seems to need Meta's
                    # app review / webhooks). Fall back to trust: ask them to follow, then send on their tap.
                    self.s.put("follow_check", "unavailable")
                    follows = None
            for row, camp in pairs:
                prompts = int(row["prompts"] or 0)
                ok = True if not camp.get("follow_gate", True) else follows
                if ok is None:
                    ok = prompts >= 1  # they've been asked once and answered: take their word
                if ok:
                    self._deliver(igsid, camp, who)
                    self.s.upsert_contact(igsid, camp["name"], stage="delivered", delivered_at=self.now())
                    delivered += 1
                elif prompts >= int(camp.get("max_prompts", 3)):
                    self.s.upsert_contact(igsid, camp["name"], stage="gave_up")
                    gave_up += 1
                else:
                    ask.append((row, camp))
            if ask:
                row, camp = ask[-1]  # the newest campaign's wording
                prompts = int(row["prompts"] or 0)
                text = camp["follow_prompt"] if prompts == 0 else camp.get("still_not_following", camp["follow_prompt"])
                button = [{"title": camp.get("follow_button", "I followed ✅"), "payload": FOLLOWED_PAYLOAD}]
                self._send("follow prompt", igsid, camp, lambda: self.g.send(igsid, text, button), f"{who}: {text}")
                for r, c in ask:
                    self.s.upsert_contact(igsid, c["name"], stage="gated", prompts=int(r["prompts"] or 0) + 1)
        except GraphError as e:
            if e.code == 230:
                # Instagram copies the comment into the new DM thread as if they'd sent it. That isn't consent:
                # wait for their real reply (the follow check only works after they message us).
                return "waiting for their reply"
            self.log(f"couldn't message {who}: {e}")
            return f"error {e.code or e.status}"
        done = []
        if delivered:
            done.append("delivered" if delivered == 1 else f"delivered {delivered}")
        if ask:
            done.append("asked to follow")
        if gave_up:
            done.append("gave up (never followed)")
        return ", ".join(done)

    # ------------------------------------------------------------------ nudges
    def nudge_due(self) -> Dict[str, int]:
        """One follow-up for people asked to follow who haven't tapped since: inside their 24 hours (they messaged us,
        so Instagram allows it), once per campaign. People who never tapped the first button can't be messaged at all."""
        counts: Dict[str, int] = {}
        by_name = {c["name"]: c for c in self.campaigns}
        due: Dict[str, List[Tuple[Any, Dict[str, Any]]]] = {}
        for r in self.s.nudge_candidates(self.now() - DAY + NUDGE_MARGIN):
            camp = by_name.get(r["campaign"])
            after = float(camp.get("nudge_after_minutes", NUDGE_AFTER_MINUTES)) if camp else 0
            if after > 0 and self.now() - (r["updated_at"] or 0) >= after * 60:
                due.setdefault(r["igsid"], []).append((r, camp))
        for igsid, pairs in due.items():
            if not self.under_cap():
                self.log("hourly cap reached: nudges wait for the next round")
                break
            got = self._nudge(igsid, pairs)
            counts[got] = counts.get(got, 0) + 1
        return counts

    def _nudge(self, igsid: str, pairs: List[Tuple[Any, Dict[str, Any]]]) -> str:
        who = f"@{pairs[-1][0]['username'] or igsid}"
        try:
            try:
                follows: Optional[bool] = bool(self.g.profile(igsid).get("is_user_follow_business"))
            except GraphError as e:
                if e.code != 230:
                    raise
                follows = None  # Instagram won't say: remind them rather than assume
            if follows:
                # They followed and never tapped "I followed": that was the point of asking, so send what they're owed.
                for _, camp in pairs:
                    self._deliver(igsid, camp, who)
                    self.s.upsert_contact(igsid, camp["name"], stage="delivered", delivered_at=self.now(), nudged_at=self.now())
                return "delivered after a nudge"
            camp = pairs[-1][1]
            text = camp.get("still_not_following", camp["follow_prompt"])
            button = [{"title": camp.get("follow_button", "I followed ✅"), "payload": FOLLOWED_PAYLOAD}]
            self._send("follow reminder", igsid, camp, lambda: self.g.send(igsid, text, button), f"{who}: {text}")
        except GraphError as e:
            self.log(f"couldn't nudge {who}: {e}")
            outcome = f"nudge error {e.code or e.status}"
        else:
            outcome = "follow reminder"
        for r, c in pairs:  # once only, even after an error: a refused nudge isn't retried every round
            if self.s.contact(igsid, c["name"])["stage"] == "gated":
                self.s.upsert_contact(igsid, c["name"], nudged_at=self.now())
        return outcome

    # ------------------------------------------------------------------ polling (no webhooks needed)
    def poll_once(self) -> Dict[str, int]:
        """One round, spending as few API calls as possible (a new Meta app gets about 200 an hour):
        comments are only fetched for a post whose comment count changed, and messages only for a
        one-to-one conversation that changed. A quiet round costs two calls. Each post is also re-read every
        `recheck_minutes` (10) whatever its count says: a deleted comment plus a new one leaves the count unchanged."""
        counts: Dict[str, int] = {}
        bump = lambda k: counts.__setitem__(k, counts.get(k, 0) + 1)  # noqa: E731
        self.started_at()
        self.s.prune(self.now() - float(self.safety.get("keep_days", 90)) * DAY)
        days = float(self.safety.get("media_days", 7))
        recheck = float(self.safety.get("recheck_minutes", 10)) * 60
        for media in self.g.recent_media(limit=int(self.safety.get("media_limit", 10))):
            if self.now() - parse_time(media.get("timestamp")) > days * DAY:
                continue
            key, count = f"comments:{media['id']}", str(media.get("comments_count"))
            read_key = f"comments_read:{media['id']}"
            fresh_read = self.now() - float(self.s.get(read_key) or 0) < recheck
            if media.get("comments_count") is not None and self.s.get(key) == count and fresh_read:
                continue
            outcomes = []
            for c in self.g.comments(media["id"]):
                frm = c.get("from") or {}
                outcomes.append(self.on_comment({"id": c["id"], "text": c.get("text"), "at": parse_time(c.get("timestamp")),
                                                 "user_id": str(frm.get("id") or ""), "username": frm.get("username") or c.get("username"),
                                                 "media_id": media["id"]}))
            for o in outcomes:
                bump(o)
            if "capped" not in outcomes:  # a held comment keeps the post on the list for next round
                self.s.put(key, count)
            self.s.put(read_key, str(self.now()))
        for conv in self.g.conversations():
            people = (conv.get("participants") or {}).get("data", [])
            if len(people) > 2:
                continue  # group chats: Instagram's API can't read them
            key, updated = f"conversation:{conv['id']}", str(conv.get("updated_time"))
            if conv.get("updated_time") and self.s.get(key) == updated:
                continue
            try:
                ids = self.g.message_ids(conv["id"])[:20]
                # Fetch only messages we could still act on (one call each), oldest first: not seen yet, and newer
                # than both the start time and Instagram's 24-hour reply window. Their ids carry the time for free.
                oldest = max(self.started_at(), self.now() - DAY)
                fresh = [m for m in ids if not self.s.message_seen(m["id"]) and parse_time(m.get("created_time")) >= oldest]
                msgs = [self.g.message(m["id"]) for m in reversed(fresh)]
            except GraphError as e:
                if e.rate_limited:
                    raise
                self.log(f"couldn't read a conversation: {e}")
                bump("unreadable conversation")
                continue
            for msg in msgs:
                frm = msg.get("from") or {}
                bump(self.on_message({"id": msg["id"], "from_id": str(frm.get("id") or ""), "username": frm.get("username"),
                                      "text": msg.get("message"), "at": parse_time(msg.get("created_time"))}))
            self.s.put(key, updated)
        for k, n in self.nudge_due().items():
            counts[k] = counts.get(k, 0) + n
        return counts
