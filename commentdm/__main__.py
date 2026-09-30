"""comment-dm: comment a keyword → public reply → DM → follow check → link. Your own ManyChat-style
auto-reply for Instagram, running on your Mac.

  python3 -m commentdm setup                 store your Instagram access token in the Keychain, then check it
  python3 -m commentdm check                 who am I, and can I read comments and DMs
  python3 -m commentdm run [--once] [--live] poll and answer (a dry run unless --live)
  python3 -m commentdm simulate              watch the whole flow against a fake Instagram, offline
  python3 -m commentdm status                what it has done so far
  python3 -m commentdm forget @username      delete everything held about one person (a deletion request)
  python3 -m commentdm refresh-token         extend the token (lasts 60 days; run weekly from launchd)
  python3 -m commentdm webhook [--port N]    receive events instead of polling (needs Meta's approval)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .config import load
from .flow import Engine
from .graph import Graph, GraphError, keychain_get
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("COMMENTDM_CONFIG", ROOT / "config.json"))
STATE = Path(os.environ.get("COMMENTDM_STATE", ROOT / "state"))


def store_for(dry_run: bool) -> Store:
    # Dry runs keep their own state, so practising never marks a real comment as answered.
    return Store(STATE / ("dry-run.sqlite" if dry_run else "live.sqlite"))


def cmd_setup() -> None:
    import getpass
    from .graph import keychain_set
    print("Paste your Instagram access token (from the Meta app's 'Generate token'). It isn't shown, and it's saved only in the Keychain.")
    token = getpass.getpass("Token: ").strip().strip('"').strip("'")
    if not token.startswith("IG"):
        raise SystemExit("That doesn't look like an Instagram token (they start with IG). Copy it again from the Meta app.")
    keychain_set("access-token", token)
    print(f"✓ saved ({len(token)} characters)")
    cmd_check()


def cmd_check() -> None:
    g = Graph()
    me = g.me()
    print(f"✓ token works: @{me.get('username')} ({me.get('account_type')}, id {me.get('user_id')})")
    for label, call in (("read your posts", lambda: g.recent_media(limit=1)), ("read your DMs", lambda: g.conversations(limit=1))):
        try:
            call()
            print(f"✓ can {label}")
        except GraphError as e:
            print(f"✗ can't {label}: {e}")
    # Comments: Instagram returns an empty list, not an error, when the token lacks the comments permission.
    try:
        posts = g._call("GET", "me/media", {"fields": "id,comments_count", "limit": 10}).get("data", [])
        with_comments = [p for p in posts if (p.get("comments_count") or 0) > 0]
        if not with_comments:
            print("? can't tell yet whether comments are readable: none of your recent posts has a comment")
        else:
            seen = sum(len(g.comments(p["id"], limit=5)) for p in with_comments[:3])
            if seen:
                print("✓ can read comments")
            else:
                total = sum(p["comments_count"] for p in with_comments[:3])
                print(f"✗ can't read comments: your posts have {total} but the app sees none. Two causes: the Meta app is still "
                      "in Development mode (switch it to Live), or the token lacks instagram_business_manage_comments (add it to the "
                      "Instagram use case, generate a new token, run setup again).")
    except GraphError as e:
        print(f"✗ can't read comments: {e}")


def cmd_run(once: bool, live: bool) -> None:
    cfg = load(CONFIG)
    dry = not live
    engine = Engine(Graph(), store_for(dry), cfg, dry_run=dry)
    every = float(cfg.get("safety", {}).get("poll_seconds", 60))
    print(f"{'LIVE' if live else 'dry run'}: checking every {every:.0f}s. Comments from before now are never answered.")
    while True:
        try:
            counts = engine.poll_once()
            acted = {k: v for k, v in counts.items() if k not in ("already handled", "already seen", "no keyword", "ignored", "before start", "own comment")}
            if acted:
                print(time.strftime("%H:%M:%S"), acted, flush=True)
        except GraphError as e:
            print(time.strftime("%H:%M:%S"), f"Instagram said: {e}", flush=True)
        except OSError as e:
            print(time.strftime("%H:%M:%S"), f"network: {e}", flush=True)
        if once:
            return
        time.sleep(every)


def cmd_simulate() -> None:
    import json
    import tempfile
    from .fake import FakeGraph
    cfg = json.loads((ROOT / "config.example.json").read_text())
    cfg["safety"]["allowlist"] = []
    g = FakeGraph()
    g.now = time.time()
    engine = Engine(g, Store(Path(tempfile.mkdtemp()) / "sim.sqlite"), cfg, dry_run=False, now=lambda: g.now, log=lambda s: None)
    engine.started_at()
    g.now += 1
    post = g.post()
    kw = cfg["campaigns"][0]["keyword"]

    def show(igsid: str, since: int) -> int:
        out = g.outbox(igsid)
        for m in out[since:]:
            print(f"   ← DM: {m['message']}")
        return len(out)

    print(f"@sam comments \"{kw.lower()} please!\" on your reel")
    g.comment(post, "201", "sam", f"{kw.lower()} please!")
    engine.poll_once()
    for call in g.calls:
        if call[0] == "reply_public":
            print(f"   ↳ public reply: {call[2]}")
    n = show("201", 0)
    print("@sam replies \"LINK\" (sam doesn't follow you yet)")
    g.now += 5
    g.dm("201", "sam", "LINK")
    engine.poll_once()
    n = show("201", n)
    print("@sam follows you, then taps the button")
    g.follows["201"] = True
    g.now += 5
    g.dm("201", "sam", cfg["campaigns"][0].get("follow_button", "I followed ✅"))
    engine.poll_once()
    show("201", n)
    print(f"\nstate: {engine.s.contact('201')['stage']}")


def cmd_status() -> None:
    for label, dry in (("live", False), ("dry run", True)):
        path = STATE / ("dry-run.sqlite" if dry else "live.sqlite")
        if path.exists():
            print(f"{label}: " + ", ".join(f"{k} {v}" for k, v in store_for(dry).stats().items()))
    if not STATE.exists():
        print("nothing yet")


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "help"
    if cmd == "setup":
        cmd_setup()
    elif cmd == "check":
        cmd_check()
    elif cmd == "run":
        cmd_run(once="--once" in args, live="--live" in args)
    elif cmd == "simulate":
        cmd_simulate()
    elif cmd == "status":
        cmd_status()
    elif cmd == "forget":
        if len(args) < 2:
            raise SystemExit("usage: forget @username")
        for dry in (False, True):
            if (STATE / ("dry-run.sqlite" if dry else "live.sqlite")).exists():
                print(f"{'dry run' if dry else 'live'}: removed {store_for(dry).forget(args[1])} records about {args[1]}")
    elif cmd == "refresh-token":
        print(Graph().refresh_token())
    elif cmd == "webhook":
        from .webhook import serve
        port = int(args[args.index("--port") + 1]) if "--port" in args else 8793
        secret = keychain_get("app-secret") or sys.exit("Store your Meta app secret first: security add-generic-password -U -s comment-dm -a app-secret -w")
        verify = keychain_get("verify-token") or sys.exit("Store a verify token first: security add-generic-password -U -s comment-dm -a verify-token -w")
        cfg = load(CONFIG)
        serve(Engine(Graph(), store_for("--live" not in args), cfg, dry_run="--live" not in args), secret, verify, port)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
