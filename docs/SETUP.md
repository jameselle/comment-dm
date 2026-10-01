# Set up comment-dm, end to end

From nothing to "someone comments CLIP and gets the link in their DMs". It takes about an hour the first time, and most of that is Meta's dashboard.

You need:

- A Mac (Python 3.9+ comes with macOS, so there's nothing to install)
- An Instagram account you'll switch to Creator or Business
- A Facebook login, for [developers.facebook.com](https://developers.facebook.com)
- A free GitHub account, to host two small pages Meta insists on
- A second Instagram account to test with (yours, or a friend's)

Meta's dashboard moves its buttons around now and then. If a label below doesn't match what you see, look for the nearest thing with the same name.

---

## 1. Make your Instagram account professional (5 min)

1. In Instagram: **Settings → Account type and tools → Switch to professional account**. Pick **Creator** or **Business**.
2. Let apps read your messages: **Settings → Messages and story replies → Message controls → Allow access to messages** → on.

## 2. Publish a privacy policy and deletion instructions (10 min)

Meta won't let your app go **Live** without a privacy policy URL and a data deletion URL. Your app has to be Live: while it's in Development mode, Instagram hides real people's comments from it (you see `comments_count: 61` and an empty list).

**Privacy policy.** `docs/privacy.html` in this repo is a template. Fill in the yellow parts (your name or business, handle, email, how long you keep records) and publish it. With GitHub Pages:

1. Create a public repo called `<your-username>.github.io`.
2. Add the filled-in file as `privacy.html`, commit, and push.
3. It appears at `https://<your-username>.github.io/privacy.html` within a minute or two.

**Data deletion instructions.** Meta's "Data deletion instructions URL" field rejected every github.io address we tried ("should represent a valid URL"). A plain GitHub repo URL worked:

1. Create a public repo, for example `datadeletion`, whose README says how someone asks for their data to be deleted (DM "delete my data" or email you) and that you'll do it within 30 days.
2. Use `https://github.com/<your-username>/datadeletion` as the URL.

When someone asks, run `python3 -m commentdm forget @their_username`.

## 3. Create the Meta app (15 min)

1. On [developers.facebook.com](https://developers.facebook.com) go to **My Apps → Create app**.
2. Use case: **Manage messaging & content on Instagram**. App type: **Business**. Give it a name people won't mind seeing, such as "<Your channel> Tools".
3. In the app, open the Instagram use case: **Instagram → API setup with Instagram business login**.
4. Under **Generate access tokens**, click **Add account**, log in with the Instagram account from step 1, and approve **all** of these:
   - `instagram_business_basic`
   - `instagram_business_manage_messages`
   - `instagram_business_manage_comments`
5. Click **Generate token** next to your account and copy it. It starts with `IG` and is about 180 characters long. Keep the tab open; you'll paste it in step 4.
6. Go to **App settings → Basic** and fill in:
   - **Privacy Policy URL**: your page from step 2
   - **User data deletion → Data deletion instructions URL**: your repo URL from step 2
   - **App domains**: `github.com` and `<your-username>.github.io`
   - **App icon** (1024 × 1024) and **Category**
   - Click **Save changes**.
7. Switch the app to **Live** (the **App Mode** toggle or **Publish** at the top of the dashboard). If Meta refuses, it lists what's missing. It's almost always something in step 6.

**You don't need App Review** for your own account. A new app gets **Standard Access**, which covers Instagram accounts you own or manage. That includes DMing strangers who comment on your posts: we tested it with an account that had no role on the app. You'd only need **Advanced Access** (App Review plus Business Verification) to run this for accounts that aren't yours.

## 4. Install comment-dm and connect it (5 min)

```sh
git clone https://github.com/jameselle/comment-dm.git
cd comment-dm
python3 -m commentdm setup
```

Paste the token when asked. It isn't shown, it goes straight into the macOS Keychain, and it's never written to a file. Don't store it with `security add-generic-password -w` by hand: that prompt silently cuts it to 128 characters and the token stops working.

`setup` then runs `check`. Every line should be a ✓:

```
✓ token works: @yourhandle (MEDIA_CREATOR, id 1784…)
✓ can read your posts
✓ can read your DMs
✓ subscribed your account to the app's webhooks (without it Instagram refuses every DM after the first)
✓ can read comments
```

**That webhook line matters even though comment-dm doesn't use webhooks.** Without the subscription, Instagram doesn't count people's DMs as reaching your app. The first DM still sends, but every DM after it fails with "outside of allowed window" and the follow check fails with "user consent is required". `check` turns the subscription on for you, and no callback URL is needed. Run `check` any time; it's safe to repeat.

## 5. Write your campaign (5 min)

```sh
cp config.example.json config.json
```

Edit `config.json`: your keyword, the messages, and up to three link buttons. The example is the real Day 1 campaign. The fields are described in the [README](../README.md#config). Leave `safety.allowlist` set to your test account for now.

To see the whole conversation without touching Instagram:

```sh
python3 -m commentdm simulate
```

## 6. Test it (10 min)

1. **Dry run.** `python3 -m commentdm run --once` logs what it *would* send and sends nothing. From your test account, comment your keyword on one of your posts first.
2. **Live, test account only.** Put your test account's username in `safety.allowlist`, then:

   ```sh
   python3 -m commentdm run --live
   ```

   Comment your keyword from the test account and go through it on the phone. The log should show:

   ```
   public reply → @tester: Sent! Check your DMs 👀
   private reply → @tester: Hey! 👋 Tap the button and I'll send you both free tools from today. [Send me the tools!]
   follow prompt → @tester: Follow me first so you don't miss day 2 (it drops tomorrow), then tap the button 👇
   link → @tester: Day 1, both free 👇 Tap to open.… [🎬 Clipper | 📱 Teleprompter]
   ```

   Tap **I followed ✅** once *without* following: you should get "I can't see the follow yet 👀". Then follow and tap again to get the links.
3. **Someone else.** Add a friend's username to the allowlist and ask them to comment. Their DM may land in their message requests; that's fine.
4. `python3 -m commentdm status` should now read `follow check ok, buttons on first DM ok, link buttons ok`.

Stop the test run with Ctrl-C.

## 7. Open it up and leave it running

1. Empty the allowlist: `"allowlist": []`.
2. Install the background service. It starts at login, restarts if it stops, and refreshes the token every Monday (tokens last 60 days):

   ```sh
   ./install-service.sh
   tail -f logs/run.log          # watch it work
   ```

3. **Keep the Mac awake.** It only answers while the Mac is on and online. If the Mac sleeps, it catches up when it wakes (comments stay answerable for 7 days), but people wait. On a laptop, plug it in and turn on **System Settings → Battery → Options → Prevent automatic sleeping on power adapter when the display is off**, or run `caffeinate -s` in a Terminal window.

Day to day:

```sh
python3 -m commentdm status             # what it has done
python3 -m commentdm forget @username   # a deletion request
./install-service.sh remove             # stop it
```

After editing `config.json`, restart it: `launchctl kickstart -k gui/$(id -u)/com.commentdm.run`.

---

## When something goes wrong

Meta's errors rarely say what's actually wrong. Everything below happened while building this.

| What you see | Cause | Fix |
|---|---|---|
| `check`: "your posts have 61 but the app sees none" | The app is in **Development mode**, which hides real comments | Step 3.7: switch it to Live |
| "Failed to decrypt" or "Invalid OAuth access token" right after storing the token | It was cut to 128 characters | Store it again with `python3 -m commentdm setup` |
| The first DM arrives, then the log says **"outside of allowed window"** (code 10, subcode 2534022) even though they just replied | Your account isn't subscribed to the app's webhooks | `python3 -m commentdm check` (it subscribes you) |
| Follow check fails: **"User consent is required"** (code 230) | Same cause as the line above | Same fix |
| Data deletion URL: "should represent a valid URL" | Meta's field refuses github.io addresses | Use a `https://github.com/...` repo URL (step 2) |
| "Application request limit reached" (code 4) | Too many calls | It pauses 15 minutes by itself. If it keeps happening, raise `poll_seconds` |
| Someone deleted their comment and commented again, and nothing happened | Their new comment left the post's comment count unchanged | Nothing to do: every post is re-read every `recheck_minutes` (10) |
| A comment from before you switched it on gets no reply | By design: turning it on never floods old comments | Answer it by hand |
| Group chats are ignored | Instagram's API can't read them | – |

## Faster replies (optional)

It checks every `poll_seconds` (15 by default), so each step takes a few seconds. For replies within about a second, Meta can push events to you instead. Your account is already subscribed (step 4). You also need a public HTTPS address for this Mac that never changes, such as **Tailscale Funnel** or a **Cloudflare named tunnel**. A quick tunnel's address changes on every restart, so it won't do.

```sh
security add-generic-password -U -s comment-dm -a app-secret -w     # App settings → Basic → App secret
security add-generic-password -U -s comment-dm -a verify-token -w   # any string; you type the same one into Meta
python3 -m commentdm webhook --port 8793 --live
```

Then in the Meta app, under the Instagram use case's webhook settings, set the callback URL to your tunnel address and the verify token you chose, and subscribe to `comments` and `messages`. Keep `run --live` going alongside as a backstop. Webhook mode has tests but hasn't been run live yet.
