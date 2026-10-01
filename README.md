# comment-dm

Your own ManyChat-style auto-reply for Instagram, running on your Mac. Someone comments a keyword on your post or reel, and comment-dm:

1. **Replies to the comment** in public ("Sent! Check your DMs 👀").
2. **DMs them** with a **Send me the tools!** button.
3. **Checks whether they follow you.** If they don't, it asks them to follow and gives them an **I followed ✅** button.
4. **Sends the links** as tap-to-open buttons once they follow.

Someone who DMs you the keyword directly skips to step 3. Ordinary DMs are never answered: those stay for you.

```
$ python3 -m commentdm simulate
@sam comments "clip please!" on your reel
   ↳ public reply: Just sent them to you 🙌
   ← DM: Hey! 👋 Tap the button and I'll send you both free tools from today.
@sam taps "Send me the tools!" (sam doesn't follow you yet)
   ← DM: Follow me first so you don't miss day 2 (it drops tomorrow), then tap the button 👇
@sam follows you, then taps the button
   ← DM: Day 1, both free 👇 Tap to open.
```

No monthly fee and no contact limit. It needs Python 3.9 or newer, which comes with macOS; there's nothing to install. It works for anyone who comments on **your own** account without Meta's App Review.

## Why the flow looks like this

These are Instagram's rules, and every tool follows them, ManyChat included:

- **The first DM to someone who only commented** is a "private reply": **one per comment**, within **7 days** of it. Meta documents it as text only, but Instagram accepts a quick-reply button on it. If it ever refuses, comment-dm falls back to text that asks them to reply.
- **You can only message them again after they reply** (tapping the button counts), and then only within **24 hours** of their last message.
- **Instagram only tells you whether they follow you** once they've messaged you.

So the first DM gets them to tap. The tap opens the conversation, and everything after that is allowed.

**If Instagram won't say who follows you** ("User consent is required", code 230), comment-dm asks them to follow once, then sends the links when they tap **I followed**, on trust. It verifies again automatically once the check works. The usual cause is a missing webhook subscription, which `check` fixes ([setup, step 4](docs/SETUP.md#4-install-comment-dm-and-connect-it-5-min)).

## Safety built in

- **Dry run by default.** `run` only logs what it would send. Add `--live` to actually send. Dry runs keep separate records, so practising never marks a real comment as answered.
- **Nothing from before you switch it on is answered.** Turning it on never floods your old comments.
- **Never twice.** Every comment and message it handles is recorded, so restarts don't cause repeats.
- **Allowlist.** Set `safety.allowlist` to your test accounts, and it answers only them.
- **Hourly cap** (`safety.max_dms_per_hour`, default 60). This counts public replies and DMs together. Anything over the cap is held until the next round, not dropped.
- **Your own comments are ignored.**
- **After a few prompts it gives up.** If someone taps "I followed" without following, it asks again up to `max_prompts` times, then stops.
- **Your token stays in the macOS Keychain.** It's never printed or written to a file.

## Set it up

**[docs/SETUP.md](docs/SETUP.md) walks through all of it**, from a fresh Instagram account to the background service, with what you should see at each step and a table of Meta's misleading errors. In short:

1. Make your Instagram account a Creator or Business account, and allow access to messages.
2. Publish a privacy policy (`docs/privacy.html` is a template) and data deletion instructions.
3. Create a Meta app (use case **Manage messaging & content on Instagram**), add your account, generate a token, fill in App settings → Basic, and switch the app to **Live**. Development mode hides real comments.
4. Connect it:

   ```sh
   git clone https://github.com/jameselle/comment-dm.git && cd comment-dm
   python3 -m commentdm setup          # paste the token; it goes into the Keychain. Then it checks everything
   cp config.example.json config.json  # your keyword, messages and links
   ```

5. Test with your second account on the allowlist: `python3 -m commentdm run --live`.
6. Empty the allowlist and leave it running: `./install-service.sh`.

**No App Review needed for your own account.** Standard Access covers Instagram accounts you own or manage, and that includes DMing strangers who comment on them. You'd only need Advanced Access (App Review plus Business Verification) to run it for someone else's account.

## Config

`config.json` holds one or more campaigns, each with its own keyword:

| Field | What it's for |
|---|---|
| `keyword` | One word, any case, matched as a whole word ("clip!" matches, "eclipse" doesn't). |
| `media` | `"all"`, or a list of post IDs to use this keyword on. |
| `public_replies` | Picked at random for the public reply. Keep each under 300 characters, and not all capitals. |
| `private_reply`, `private_reply_button` | The first DM and its button (20 characters at most). Tapping it opens the conversation. |
| `private_reply_plain` | Used if Instagram ever refuses the button: **ask them to reply**, or the conversation can't continue. |
| `follow_gate` | `true` to require a follow before sending the link. |
| `follow_prompt`, `follow_button`, `still_not_following` | The follow ask, its button (20 characters at most) and the re-ask. |
| `deliver`, `deliver_buttons` | The message with your links, and up to 3 tap-to-open buttons (`title` 20 characters at most, `url` https). |
| `deliver_plain` | The links as text, used if Instagram refuses buttons, and as the private reply when someone who already has them comments again. |
| `max_prompts` | How many times to ask someone to follow before giving up. |
| `safety.*` | `allowlist`, `max_dms_per_hour`, `poll_seconds`, `media_days` (only posts from the last N days), `media_limit` (how many recent posts to watch), `keep_days` (records are deleted after this many days; default 90), `recheck_minutes` (re-read every watched post this often even if its comment count hasn't changed, which catches a deleted comment replaced by a new one; default 10). |

## Tests

```sh
python3 -m unittest discover -s tests
```

The tests run against a fake Instagram that enforces the real rules: one private reply per comment within 7 days, other DMs only within 24 hours of the person's last message, and the follow check only after they've messaged.

## Licence

MIT. See [LICENSE](LICENSE).
