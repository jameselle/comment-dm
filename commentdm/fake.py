"""A stand-in for Instagram that follows the real API's rules, for the tests and `simulate`:
one private reply per comment within 7 days, other DMs only within 24 hours of the person's last
message, the follow check only after they've messaged."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .graph import GraphError

DAY = 86400


class FakeGraph:
    def __init__(self, me: str = "100", now: float = 1_800_000_000.0):
        self.my_id, self.now = me, now
        self.media: List[Dict[str, Any]] = []
        self.comments_by_media: Dict[str, List[Dict[str, Any]]] = {}
        self.inbox: Dict[str, List[Dict[str, Any]]] = {}  # igsid -> messages, newest first
        self.follows: Dict[str, bool] = {}
        self.private_replied: Dict[str, str] = {}  # comment id -> igsid
        self.calls: List[tuple] = []
        self.reads: List[tuple] = []  # API reads, kept apart from sends
        self._n = 0

    # ---- test helpers
    def _id(self) -> str:
        self._n += 1
        return str(1000 + self._n)

    def iso(self, t: float) -> str:
        import datetime as dt
        return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")

    def post(self, at: Optional[float] = None) -> str:
        mid = self._id()
        self.media.insert(0, {"id": mid, "timestamp": self.iso(at or self.now)})
        self.comments_by_media[mid] = []
        return mid

    def comment(self, media_id: str, igsid: str, username: str, text: str, at: Optional[float] = None) -> str:
        cid = self._id()
        self.comments_by_media[media_id].insert(0, {"id": cid, "text": text, "timestamp": self.iso(at or self.now),
                                                    "from": {"id": f"u{igsid}", "username": username}, "_igsid": igsid})
        return cid

    def dm(self, igsid: str, username: str, text: str, at: Optional[float] = None) -> str:
        mid = f"m{self._id()}"
        self.inbox.setdefault(igsid, []).insert(0, {"id": mid, "from": {"id": igsid, "username": username}, "message": text,
                                                    "created_time": self.iso(at or self.now)})
        return mid

    def outbox(self, igsid: str) -> List[Dict[str, Any]]:
        return [m for m in reversed(self.inbox.get(igsid, [])) if m["from"]["id"] == self.my_id]

    def _last_inbound(self, igsid: str) -> Optional[float]:
        from .flow import parse_time
        for m in self.inbox.get(igsid, []):
            if m["from"]["id"] != self.my_id and not m.get("_echo_of_comment"):
                return parse_time(m["created_time"])
        return None

    # ---- the API surface Graph offers
    def me(self) -> Dict[str, Any]:
        return {"user_id": self.my_id, "username": "me"}

    def recent_media(self, limit: int = 10) -> List[Dict[str, Any]]:
        self.reads.append(("recent_media",))
        return [{**m, "comments_count": len(self.comments_by_media.get(m["id"], []))} for m in self.media[:limit]]

    def comments(self, media_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        self.reads.append(("comments", media_id))
        return [{k: v for k, v in c.items() if not k.startswith("_")} for c in self.comments_by_media.get(media_id, [])][:limit]

    def reply_public(self, comment_id: str, text: str) -> Dict[str, Any]:
        self.calls.append(("reply_public", comment_id, text))
        return {"id": self._id()}

    refuse_private_reply_buttons = False
    refuse_link_buttons = False

    def private_reply(self, comment_id: str, text: str, quick_replies: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        from .flow import parse_time
        if quick_replies and self.refuse_private_reply_buttons:
            raise GraphError(400, {"error": {"message": "quick replies aren't supported on private replies", "code": 100}})
        self.calls.append(("private_reply", comment_id, text, quick_replies))
        c = next((c for cs in self.comments_by_media.values() for c in cs if c["id"] == comment_id), None)
        if c is None:
            raise GraphError(400, {"error": {"message": "no such comment", "code": 100}})
        if comment_id in self.private_replied:
            raise GraphError(400, {"error": {"message": "only one private reply per comment", "code": 10, "error_subcode": 2018278}})
        if self.now - parse_time(c["timestamp"]) > 7 * DAY:
            raise GraphError(400, {"error": {"message": "outside the 7 day window", "code": 10, "error_subcode": 2534022}})
        igsid = c["_igsid"]
        self.private_replied[comment_id] = igsid
        # Like Instagram: the comment shows up in the new DM thread, attributed to the commenter, but it isn't consent.
        self.inbox.setdefault(igsid, []).insert(0, {"id": f"m{self._id()}", "from": {"id": igsid, "username": c["from"]["username"]},
                                                    "message": c["text"], "created_time": c["timestamp"], "_echo_of_comment": True})
        self.inbox.setdefault(igsid, []).insert(0, {"id": f"m{self._id()}", "from": {"id": self.my_id, "username": "me"},
                                                    "message": text, "created_time": self.iso(self.now), "_quick_replies": quick_replies})
        return {"recipient_id": igsid, "message_id": self._id()}

    def send_link_buttons(self, igsid: str, text: str, buttons: List[Dict[str, str]]) -> Dict[str, Any]:
        if self.refuse_link_buttons:
            raise GraphError(400, {"error": {"message": "templates not allowed", "code": 100}})
        self.calls.append(("send_link_buttons", igsid, text, buttons))
        last = self._last_inbound(igsid)
        if last is None or self.now - last > DAY:
            raise GraphError(400, {"error": {"message": "outside the 24 hour window", "code": 10, "error_subcode": 2534022}})
        self.inbox.setdefault(igsid, []).insert(0, {"id": f"m{self._id()}", "from": {"id": self.my_id, "username": "me"},
                                                    "message": text, "created_time": self.iso(self.now), "_buttons": buttons})
        return {"recipient_id": igsid, "message_id": self._id()}

    def send(self, igsid: str, text: str, quick_replies: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        self.calls.append(("send", igsid, text, quick_replies))
        last = self._last_inbound(igsid)
        if last is None or self.now - last > DAY:
            raise GraphError(400, {"error": {"message": "outside the 24 hour window", "code": 10, "error_subcode": 2534022}})
        self.inbox.setdefault(igsid, []).insert(0, {"id": f"m{self._id()}", "from": {"id": self.my_id, "username": "me"},
                                                    "message": text, "created_time": self.iso(self.now), "_quick_replies": quick_replies})
        return {"recipient_id": igsid, "message_id": self._id()}

    def profile(self, igsid: str) -> Dict[str, Any]:
        self.calls.append(("profile", igsid))
        if self._last_inbound(igsid) is None:
            raise GraphError(400, {"error": {"message": "no consent: the user hasn't messaged you", "code": 230}})
        return {"username": f"user{igsid}", "is_user_follow_business": self.follows.get(igsid, False)}

    groups: List[str] = []

    def conversations(self, limit: int = 25) -> List[Dict[str, Any]]:
        self.reads.append(("conversations",))
        out = [{"id": f"c{igsid}", "updated_time": msgs[0]["created_time"] + f"#{len(msgs)}",
                "participants": {"data": [{"id": self.my_id}, {"id": igsid}]}} for igsid, msgs in self.inbox.items() if msgs]
        out += [{"id": f"g{g}", "updated_time": "x", "participants": {"data": [{"id": self.my_id}, {"id": "a"}, {"id": "b"}]}} for g in self.groups]
        return out[:limit]

    def message_ids(self, conversation_id: str) -> List[Dict[str, Any]]:
        self.reads.append(("message_ids", conversation_id))
        if conversation_id.startswith("g"):
            raise GraphError(400, {"error": {"message": "Unsupported get request", "code": 100}})
        return [{"id": m["id"], "created_time": m["created_time"]} for m in self.inbox.get(conversation_id[1:], [])]

    def message(self, message_id: str) -> Dict[str, Any]:
        self.reads.append(("message", message_id))
        for msgs in self.inbox.values():
            for m in msgs:
                if m["id"] == message_id:
                    return {k: v for k, v in m.items() if not k.startswith("_")}
        raise GraphError(400, {"error": {"message": "no such message", "code": 100}})

    def messages(self, conversation_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return [{k: v for k, v in m.items() if not k.startswith("_")} for m in self.inbox.get(conversation_id[1:], [])][:limit]
