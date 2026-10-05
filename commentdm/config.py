"""Campaigns (keyword → messages) and safety settings, from config.json. Validated up front, so a typo
fails at start rather than halfway through someone's DMs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

REQUIRED = ["name", "keyword", "private_reply", "follow_prompt", "deliver"]


def validate(cfg: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    camps = cfg.get("campaigns")
    if not isinstance(camps, list) or not camps:
        return ["campaigns: at least one"]
    names = set()
    for i, c in enumerate(camps):
        for k in REQUIRED:
            if not isinstance(c.get(k), str) or not c[k].strip():
                errors.append(f"campaigns[{i}].{k}: required text")
        if c.get("name") in names:
            errors.append(f"campaigns[{i}].name: duplicate")
        names.add(c.get("name"))
        if " " in str(c.get("keyword", "")).strip():
            errors.append(f"campaigns[{i}].keyword: one word, so people can type it")
        if len(str(c.get("follow_button", "I followed ✅"))) > 20:
            errors.append(f"campaigns[{i}].follow_button: 20 characters at most (Instagram cuts the rest)")
        for r in c.get("public_replies", []):
            if len(r) > 300 or r.upper() == r and any(ch.isalpha() for ch in r):
                errors.append(f"campaigns[{i}].public_replies: under 300 characters and not all capitals (Instagram refuses both)")
        if c.get("private_reply_button"):
            if len(c["private_reply_button"]) > 20:
                errors.append(f"campaigns[{i}].private_reply_button: 20 characters at most")
            if not str(c.get("private_reply_plain", "")).strip():
                errors.append(f"campaigns[{i}].private_reply_plain: needed with a button, in case Instagram refuses buttons on private replies (ask them to reply)")
        if c.get("deliver_buttons") is not None:
            b = c["deliver_buttons"]
            if not (isinstance(b, list) and 1 <= len(b) <= 3 and all(isinstance(x, dict) and str(x.get("url", "")).startswith("https://") and 0 < len(str(x.get("title", ""))) <= 20 for x in b)):
                errors.append(f"campaigns[{i}].deliver_buttons: 1 to 3 buttons, each with a title of 20 characters at most and an https:// url")
            if not str(c.get("deliver_plain", "")).strip():
                errors.append(f"campaigns[{i}].deliver_plain: needed with deliver_buttons (the links as text, in case buttons are refused)")
            if len(str(c.get("deliver", ""))) > 640:
                errors.append(f"campaigns[{i}].deliver: 640 characters at most when it goes with buttons")
        if "nudge_after_minutes" in c and not (isinstance(c["nudge_after_minutes"], (int, float)) and 0 <= c["nudge_after_minutes"] < 23 * 60):
            errors.append(f"campaigns[{i}].nudge_after_minutes: 0 (off) up to 1379, inside Instagram's 24 hours")
        media = c.get("media", "all")
        if media != "all" and not (isinstance(media, list) and all(isinstance(m, str) for m in media)):
            errors.append(f"campaigns[{i}].media: \"all\" or a list of media ids")
    s = cfg.get("safety", {})
    for k in ("max_dms_per_hour", "poll_seconds", "media_days", "media_limit", "keep_days", "recheck_minutes"):
        if k in s and not (isinstance(s[k], (int, float)) and s[k] > 0):
            errors.append(f"safety.{k}: a positive number")
    if "allowlist" in s and not (isinstance(s["allowlist"], list) and all(isinstance(u, str) for u in s["allowlist"])):
        errors.append("safety.allowlist: a list of usernames")
    return errors


def load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"No config at {path}. Copy config.example.json to config.json and edit it.")
    cfg = json.loads(path.read_text())
    errors = validate(cfg)
    if errors:
        raise SystemExit("config.json has problems:\n  - " + "\n  - ".join(errors))
    return cfg
