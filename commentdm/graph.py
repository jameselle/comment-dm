"""The Instagram API with Instagram Login (graph.instagram.com): only the calls comment-dm needs.

The access token lives in the macOS Keychain (service "comment-dm", account "access-token") and is never
printed or written to disk by this code.
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

API = "https://graph.instagram.com/v25.0"
KEYCHAIN_SERVICE = "comment-dm"


class GraphError(Exception):
    def __init__(self, status: int, body: Dict[str, Any]):
        err = body.get("error", {}) if isinstance(body, dict) else {}
        self.status = status
        self.code = err.get("code")
        self.subcode = err.get("error_subcode")
        super().__init__(f"HTTP {status}: {err.get('message') or body}")


def keychain_get(account: str) -> Optional[str]:
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def keychain_set(account: str, secret: str) -> None:
    # -U updates an existing item. The secret goes in through argv to `security`, which is local to this Mac.
    subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", account, "-w", secret],
                   check=True, capture_output=True)


class Graph:
    """Thin client. Every method is one documented call; errors raise GraphError with Meta's code."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or keychain_get("access-token")
        if not self.token:
            raise SystemExit("No access token in the Keychain. Run: python3 -m commentdm setup")

    def _call(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        q = dict(params or {})
        q["access_token"] = self.token
        url = f"{API}/{path.lstrip('/')}?{urllib.parse.urlencode(q)}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"{}")
            except ValueError:
                payload = {}
            raise GraphError(e.code, payload) from None

    # ---- account
    def me(self) -> Dict[str, Any]:
        return self._call("GET", "me", {"fields": "user_id,username,account_type"})

    # ---- posts and comments
    def recent_media(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._call("GET", "me/media", {"fields": "id,caption,timestamp,permalink,media_product_type", "limit": limit}).get("data", [])

    def comments(self, media_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self._call("GET", f"{media_id}/comments", {"fields": "id,text,timestamp,from,username", "limit": limit}).get("data", [])

    def reply_public(self, comment_id: str, text: str) -> Dict[str, Any]:
        return self._call("POST", f"{comment_id}/replies", {"message": text})

    # ---- messages
    def private_reply(self, comment_id: str, text: str) -> Dict[str, Any]:
        """One plain-text DM to someone who commented, within 7 days of the comment."""
        return self._call("POST", "me/messages", body={"recipient": {"comment_id": comment_id}, "message": {"text": text}})

    def send(self, igsid: str, text: str, quick_replies: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """A DM inside the 24 hours after the person last messaged us."""
        message: Dict[str, Any] = {"text": text}
        if quick_replies:
            message["quick_replies"] = [{"content_type": "text", "title": q["title"][:20], "payload": q["payload"]} for q in quick_replies]
        return self._call("POST", "me/messages", body={"recipient": {"id": igsid}, "message": message})

    def profile(self, igsid: str) -> Dict[str, Any]:
        """Needs the person to have messaged us first. is_user_follow_business: do they follow us."""
        return self._call("GET", igsid, {"fields": "username,is_user_follow_business"})

    def conversations(self, limit: int = 25) -> List[Dict[str, Any]]:
        return self._call("GET", "me/conversations", {"platform": "instagram", "fields": "id,updated_time", "limit": limit}).get("data", [])

    def messages(self, conversation_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        got = self._call("GET", conversation_id, {"fields": f"messages.limit({limit}){{id,from,message,created_time}}"})
        return got.get("messages", {}).get("data", [])

    # ---- token upkeep: long-lived tokens last 60 days and can be refreshed once they're a day old
    def refresh_token(self) -> Dict[str, Any]:
        q = urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": self.token})
        with urllib.request.urlopen(f"https://graph.instagram.com/refresh_access_token?{q}", timeout=30) as resp:
            got = json.loads(resp.read())
        if got.get("access_token"):
            keychain_set("access-token", got["access_token"])
            self.token = got["access_token"]
        return {"expires_in": got.get("expires_in")}
