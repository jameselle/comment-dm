# comment-dm

Your own ManyChat-style auto-reply for Instagram, running on your Mac. Someone comments a keyword on your post or reel, and comment-dm:

1. **Replies to the comment** in public ("Sent! Check your DMs 👀").
2. **DMs them**, asking them to reply to get the link.
3. **Checks whether they follow you.** If they don't, it asks them to follow and gives them an **I followed ✅** button.
4. **Sends the link** once they follow.

Someone who DMs you the keyword directly skips to step 3. Ordinary DMs are never answered: those stay for you.

```
$ python3 -m commentdm simulate
@sam comments "clip please!" on your reel
   ↳ public reply: Just sent them to you 🙌
   ← DM: Hey! 👋 Reply LINK and I'll send you both free tools from today.
@sam replies "LINK" (sam doesn't follow you yet)
   ← DM: Follow me first so you don't miss day 2 (it drops tomorrow), then tap the button 👇
@sam follows you, then taps the button
   ← DM: Day 1, both free: …
```

No monthly fee and no contact limit. It needs Python 3.9 or newer, which comes with macOS; there's nothing to install.

## Why the flow looks like this

These are Instagram's rules, and every tool follows them, ManyChat included:

- **The first DM to someone who only commented** (a "private reply") must be plain text, with no buttons. You get **one per comment**, within **7 days** of it.
- **You can only message them again after they reply**, and then only within **24 hours** of their last message.
- **Instagram only tells you whether they follow you** once they've messaged you.

So the first DM asks them to reply. Their reply opens the conversation, and everything after that is allowed.

**If Instagram won't say who follows you.** On a new app, Instagram can refuse the follow check even after someone has replied ("User consent is required", code 230). It seems to need Meta's app review. When that happens, comment-dm asks them to follow once, then sends the link when they tap **I followed**, on trust. Once the check works, it verifies again automatically. `python3 -m commentdm status` shows which mode it's in.

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

### 1. An Instagram professional account

It must be a Creator or Business account. In Instagram: Settings → *Account type and tools* → *Switch to professional account*. Then allow message access: Settings → *Messages and story replies* → *Message controls* → *Allow access to messages*.

### 2. A Meta app (free)

1. Go to [developers.facebook.com](https://developers.facebook.com), open **My Apps**, then **Create app**.
2. Choose the use case **Manage messaging & content on Instagram**, then **Business** as the app type.
3. In the app, open **Instagram → API setup with Instagram business login**. Under **Generate access tokens**, click **Add account** and log in with your Instagram account. Approve **instagram_business_basic**, **instagram_business_manage_messages** and **instagram_business_manage_comments**.
4. Click **Generate token** and copy it. It lasts 60 days, and comment-dm refreshes it for you once the service is installed.

### 3. Connect it

```sh
git clone <this repo> comment-dm && cd comment-dm
python3 -m commentdm setup          # paste the token when asked; it goes into the Keychain
cp config.example.json config.json  # then edit your keyword, messages and links
```

`setup` finishes by checking that the token can read your posts and your DMs.

### 4. Test before it goes live

1. **Dry run.** `python3 -m commentdm run --once` shows what it would send for new keyword comments. Comment your keyword from another account to see it.
2. **Live, testers only.** Put your second account in `safety.allowlist`. In the Meta app, add that account under **App roles → Roles → Instagram Testers**. Accept the invite on Instagram (Settings → *Website permissions* → *Apps and websites* → *Tester invites*). Then `python3 -m commentdm run --live`, comment the keyword from the tester account, and go through the whole flow.
3. **Open it up.** Empty the allowlist and run live.

### 5. Leave it running

```sh
./install-service.sh          # runs it in the background, and refreshes the token every Monday
./install-service.sh remove   # stop it
python3 -m commentdm status   # what it has done
python3 -m commentdm forget @username   # someone asked for their data to be deleted
```

## Meta's approval (App Review)

Meta gives a new app **Standard Access**, which covers *Instagram accounts you own or manage*: your own account. Whether Standard Access is enough to reply to strangers who comment varies, and your testers-only run won't show it, because testers have a role on the app.

In practice, open it up and check whether a stranger's comment gets its DM. If Instagram answers with a permissions error (`python3 -m commentdm status` and `logs/run.log` show it), submit your app for review:

- In **App Review → Permissions and features**, request **Advanced Access** for `instagram_business_manage_messages` and `instagram_business_manage_comments`.
- Complete **Business Verification** when Meta asks for it.
- Add a **privacy policy URL**. `docs/privacy.html` is a starting point: fill it in and publish it, for example with GitHub Pages.
- Record a **screencast** of the flow, the testers-only run from step 4.2.

### Instant replies instead of checking every minute (optional)

By default it checks for new comments and DMs every `poll_seconds` (60). For instant replies, Meta can push events to you (webhooks). That needs an approved, **Live** app with Advanced Access and business verification, plus a public HTTPS address in front of this Mac, such as Cloudflare Tunnel or Tailscale Funnel. Then:

```sh
security add-generic-password -U -s comment-dm -a app-secret -w     # your Meta app secret
security add-generic-password -U -s comment-dm -a verify-token -w   # any string you also enter in Meta's webhook setup
python3 -m commentdm webhook --port 8793 --live
```

In the Meta app, set the callback URL to your tunnel address, and subscribe to `comments` and `messages`.

## Config

`config.json` holds one or more campaigns, each with its own keyword:

| Field | What it's for |
|---|---|
| `keyword` | One word, any case, matched as a whole word ("clip!" matches, "eclipse" doesn't). |
| `media` | `"all"`, or a list of post IDs to use this keyword on. |
| `public_replies` | Picked at random for the public reply. Keep each under 300 characters, and not all capitals. |
| `private_reply` | The first DM. Plain text. **Ask them to reply**, or the conversation can't continue. |
| `follow_gate` | `true` to require a follow before sending the link. |
| `follow_prompt`, `follow_button`, `still_not_following` | The follow ask, its button (20 characters at most) and the re-ask. |
| `deliver` | The message with your link(s). |
| `max_prompts` | How many times to ask someone to follow before giving up. |
| `safety.*` | `allowlist`, `max_dms_per_hour`, `poll_seconds`, `media_days` (only posts from the last N days), `media_limit` (how many recent posts to watch), `keep_days` (records are deleted after this many days; default 90), `recheck_minutes` (re-read every watched post this often even if its comment count hasn't changed, which catches a deleted comment replaced by a new one; default 10). |

## Tests

```sh
python3 -m unittest discover -s tests
```

The tests run against a fake Instagram that enforces the real rules: one private reply per comment within 7 days, other DMs only within 24 hours of the person's last message, and the follow check only after they've messaged.

## Licence

MIT. See [LICENSE](LICENSE).
