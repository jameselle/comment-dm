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
        media = c.get("media", "all")
        if media != "all" and not (isinstance(media, list) and all(isinstance(m, str) for m in media)):
            errors.append(f"campaigns[{i}].media: \"all\" or a list of media ids")
    s = cfg.get("safety", {})
    for k in ("max_dms_per_hour", "poll_seconds", "media_days", "media_limit", "keep_days"):
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
