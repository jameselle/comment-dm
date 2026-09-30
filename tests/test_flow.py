"""The whole flow against a fake Instagram that enforces the real API's rules (one private reply per
comment within 7 days, other DMs only within 24 hours of the person's last message, the follow check
only after they've messaged)."""
import copy
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from commentdm.config import validate
from commentdm.fake import FakeGraph
from commentdm.flow import FOLLOWED_PAYLOAD, Engine, keyword_in
from commentdm.store import Store
from commentdm.webhook import events, signature_ok

EXAMPLE = json.loads((Path(__file__).resolve().parent.parent / "config.example.json").read_text())
DAY = 86400


class Base(unittest.TestCase):
    def make(self, dry_run=False, **safety):
        cfg = copy.deepcopy(EXAMPLE)
        cfg["safety"].update(safety)
        self.g = FakeGraph()
        self.logs = []
        self.e = Engine(self.g, Store(Path(tempfile.mkdtemp()) / "s.sqlite"), cfg, dry_run=dry_run,
                        now=lambda: self.g.now, log=self.logs.append)
        self.e.started_at()   # switched on now
        self.g.now += 10      # everything below happens after
        self.post = self.g.post()
        self.camp = cfg["campaigns"][0]
        return self.e

    def kinds(self):
        reads = ("recent_media", "comments", "conversations", "messages", "profile")
        return [c[0] for c in self.g.calls if c[0] not in reads]


class CommentFlow(Base):
    def test_keyword_comment_gets_a_public_reply_and_one_private_reply(self):
        self.make()
        self.g.comment(self.post, "201", "sam", "clip please!")
        self.e.poll_once()
        self.assertEqual(self.kinds(), ["reply_public", "private_reply"])
        self.assertIn(self.g.calls[0][2], self.camp["public_replies"])
        self.assertEqual(self.g.outbox("201")[0]["message"], self.camp["private_reply"])
        self.assertEqual(self.e.s.contact("201")["stage"], "awaiting_reply")

    def test_polling_again_never_replies_twice(self):
        self.make()
        self.g.comment(self.post, "201", "sam", "CLIP")
        self.e.poll_once()
        self.e.poll_once()
        self.e.poll_once()
        self.assertEqual(self.kinds(), ["reply_public", "private_reply"])

    def test_old_comments_own_comments_and_other_words_are_left_alone(self):
        self.make()
        self.g.comment(self.post, "202", "old", "CLIP", at=self.g.now - 60)        # before it was switched on
        self.g.comment(self.post, self.g.my_id, "me", "CLIP")                      # hmm: own id is on 'from' as u<id>
        self.g.comments_by_media[self.post][0]["from"]["id"] = self.g.my_id        # make it really ours
        self.g.comment(self.post, "203", "eve", "love the eclipse shot")           # 'clip' inside a word
        self.e.poll_once()
        self.assertEqual(self.kinds(), [])
        stored = {r["username"]: r["outcome"] for r in self.e.s.db.execute("select username, outcome from comments")}
        self.assertEqual(stored, {"old": "before start", "me": "own comment", "eve": "no keyword"})

    def test_keyword_matching(self):
        for text, want in (("CLIP", True), ("clip!", True), ("Clip please", True), ("#clip", True), ("eclipse", False), ("clips", False), ("", False)):
            self.assertEqual(keyword_in("CLIP", text), want, text)

    def test_allowlist_limits_who_gets_answered(self):
        self.make(allowlist=["@Tester"])
        self.g.comment(self.post, "201", "stranger", "CLIP")
        self.g.comment(self.post, "202", "tester", "CLIP")
        self.e.poll_once()
        self.assertEqual(self.kinds(), ["reply_public", "private_reply"])
        self.assertIn("202", self.g.inbox)
        self.assertNotIn("201", self.g.inbox)

    def test_a_failed_private_reply_is_recorded_and_the_rest_carry_on(self):
        self.make()
        bad = self.g.comment(self.post, "201", "sam", "CLIP")
        self.g.private_replied[bad] = "201"   # Instagram already has a private reply on this comment
        self.g.comment(self.post, "202", "ann", "CLIP")
        counts = self.e.poll_once()
        self.assertEqual(counts.get("replied"), 1)
        self.assertTrue(any(k.startswith("error") for k in counts), counts)


class DmFlow(Base):
    def start(self, follows=False):
        self.make()
        self.g.follows["201"] = follows
        self.g.comment(self.post, "201", "sam", "CLIP")
        self.e.poll_once()
        self.g.now += 30
        self.g.dm("201", "sam", "LINK")
        self.e.poll_once()

    def test_a_follower_gets_the_link_straight_after_replying(self):
        self.start(follows=True)
        self.assertEqual(self.g.outbox("201")[-1]["message"], self.camp["deliver"])
        self.assertEqual(self.e.s.contact("201")["stage"], "delivered")

    def test_a_non_follower_is_asked_to_follow_with_a_button_then_gets_the_link(self):
        self.start(follows=False)
        prompt = self.g.inbox["201"][0]
        self.assertEqual(prompt["message"], self.camp["follow_prompt"])
        self.assertEqual(prompt["_quick_replies"][0]["payload"], FOLLOWED_PAYLOAD)
        self.assertLessEqual(len(prompt["_quick_replies"][0]["title"]), 20)
        self.g.follows["201"] = True
        self.g.now += 30
        self.g.dm("201", "sam", self.camp["follow_button"])
        self.e.poll_once()
        self.assertEqual(self.g.outbox("201")[-1]["message"], self.camp["deliver"])

    def test_tapping_without_following_asks_again_then_stops_after_max_prompts(self):
        self.start(follows=False)
        for i in range(5):
            self.g.now += 30
            self.g.dm("201", "sam", "I followed ✅")
            self.e.poll_once()
        sent = [m["message"] for m in self.g.outbox("201")]
        self.assertEqual(sent.count(self.camp["follow_prompt"]), 1)
        self.assertEqual(sent.count(self.camp["still_not_following"]), 2)
        self.assertNotIn(self.camp["deliver"], sent)
        self.assertEqual(self.e.s.contact("201")["stage"], "gave_up")

    def test_after_delivery_further_messages_are_left_for_a_human(self):
        self.start(follows=True)
        before = len(self.kinds())
        self.g.now += 30
        self.g.dm("201", "sam", "thanks legend, CLIP again?")
        self.e.poll_once()
        self.assertEqual(len(self.kinds()), before)

    def test_the_comment_copied_into_the_dm_thread_is_not_treated_as_their_reply(self):
        self.make()
        self.g.follows["201"] = True
        self.g.comment(self.post, "201", "sam", "CLIP")
        counts = self.e.poll_once()   # private reply goes out; Instagram echoes the comment into the thread
        self.e.poll_once()
        self.assertEqual(counts.get("waiting for their reply"), 1)
        self.assertNotIn(self.camp["deliver"], [m["message"] for m in self.g.outbox("201")])
        self.assertEqual(self.e.s.contact("201")["stage"], "awaiting_reply")
        self.g.now += 30
        self.g.dm("201", "sam", "LINK")   # their real reply
        self.e.poll_once()
        self.assertEqual(self.g.outbox("201")[-1]["message"], self.camp["deliver"])

    def test_dming_the_keyword_directly_starts_the_flow_too(self):
        self.make()
        self.g.follows["301"] = True
        self.g.dm("301", "dan", "clip")
        self.e.poll_once()
        self.assertEqual(self.g.outbox("301")[-1]["message"], self.camp["deliver"])

    def test_an_ordinary_dm_is_never_answered(self):
        self.make()
        self.g.dm("401", "friend", "hey mate how's it going")
        self.e.poll_once()
        self.assertEqual(self.kinds(), [])


class Safety(Base):
    def test_dry_run_sends_nothing(self):
        self.make(dry_run=True)
        self.g.comment(self.post, "201", "sam", "CLIP")
        self.e.poll_once()
        self.assertEqual(self.kinds(), [])
        self.assertTrue(any("[dry run] private reply" in l for l in self.logs))

    def test_hourly_cap_holds_comments_for_the_next_round(self):
        self.make(max_dms_per_hour=2)  # one comment = public reply + private reply
        self.g.comment(self.post, "201", "a", "CLIP")
        self.g.comment(self.post, "202", "b", "CLIP")
        self.e.poll_once()
        self.assertEqual(self.kinds().count("private_reply"), 1)
        self.g.now += 3700  # an hour later the held comment goes out
        self.e.poll_once()
        self.assertEqual(self.kinds().count("private_reply"), 2)

    def test_comments_older_than_seven_days_arent_private_replied(self):
        self.make()
        self.g.comment(self.post, "201", "sam", "CLIP", at=self.g.now)
        self.g.now += 8 * DAY
        self.e.poll_once()
        self.assertNotIn("private_reply", self.kinds())


class Buttons(Base):
    def test_first_dm_has_a_send_me_the_tools_button(self):
        self.make()
        self.g.comment(self.post, "201", "sam", "CLIP")
        self.e.poll_once()
        dm = self.g.outbox("201")[0]
        self.assertEqual(dm["message"], self.camp["private_reply"])
        self.assertEqual(dm["_quick_replies"][0]["title"], "Send me the tools!")

    def test_if_instagram_refuses_the_button_the_plain_text_version_goes_and_is_used_from_then_on(self):
        self.make()
        self.g.refuse_private_reply_buttons = True
        self.g.comment(self.post, "201", "sam", "CLIP")
        self.g.comment(self.post, "202", "ann", "CLIP")
        self.e.poll_once()
        self.assertEqual(self.g.outbox("201")[0]["message"], self.camp["private_reply_plain"])
        self.assertEqual(self.g.outbox("202")[0]["message"], self.camp["private_reply_plain"])
        self.assertEqual(self.e.s.get("private_reply_buttons"), "refused")

    def test_tapping_the_button_counts_as_their_reply(self):
        self.make()
        self.g.follows["201"] = True
        self.g.comment(self.post, "201", "sam", "CLIP")
        self.e.poll_once()
        self.g.now += 30
        self.g.dm("201", "sam", "Send me the tools!")
        self.e.poll_once()
        self.assertEqual(self.g.outbox("201")[-1]["_buttons"], self.camp["deliver_buttons"])

    def test_links_go_as_text_if_link_buttons_are_refused(self):
        self.make()
        self.g.refuse_link_buttons = True
        self.g.follows["201"] = True
        self.g.comment(self.post, "201", "sam", "CLIP")
        self.e.poll_once()
        self.g.now += 30
        self.g.dm("201", "sam", "Send me the tools!")
        self.e.poll_once()
        self.assertEqual(self.g.outbox("201")[-1]["message"], self.camp["deliver_plain"])


class ApiBudget(Base):
    def test_a_quiet_round_costs_two_calls_and_group_chats_are_skipped(self):
        self.make()
        self.g.groups = ["team"]
        self.g.comment(self.post, "201", "sam", "hello")
        self.e.poll_once()
        self.g.reads.clear()
        self.e.poll_once()   # nothing changed
        self.assertEqual([c[0] for c in self.g.reads], ["recent_media", "conversations"])

    def test_only_the_post_that_changed_is_read(self):
        self.make()
        other = self.g.post()
        self.e.poll_once()
        self.g.reads.clear()
        self.g.comment(other, "201", "sam", "CLIP")
        self.e.poll_once()
        self.assertEqual([c for c in self.g.reads if c[0] == "comments"], [("comments", other)])

    def test_rate_limit_is_recognised(self):
        from commentdm.graph import GraphError
        self.assertTrue(GraphError(403, {"error": {"message": "Application request limit reached", "code": 4}}).rate_limited)
        self.assertFalse(GraphError(400, {"error": {"message": "Unsupported get request", "code": 100}}).rate_limited)


class Retention(Base):
    def test_records_older_than_keep_days_are_deleted(self):
        self.make(keep_days=30)
        self.g.comment(self.post, "201", "sam", "CLIP")
        self.e.poll_once()
        self.assertTrue(self.e.s.contact("201"))
        self.g.now += 31 * DAY
        self.e.poll_once()
        self.assertIsNone(self.e.s.contact("201"))
        self.assertEqual(self.e.s.db.execute("select count(*) from comments").fetchone()[0], 0)

    def test_forget_removes_one_person_and_nobody_else(self):
        self.make()
        self.g.comment(self.post, "201", "sam", "CLIP")
        self.g.comment(self.post, "202", "ann", "CLIP")
        self.e.poll_once()
        self.assertGreater(self.e.s.forget("@Sam"), 0)
        self.assertIsNone(self.e.s.contact("201"))
        self.assertTrue(self.e.s.contact("202"))
        users = [r[0] for r in self.e.s.db.execute("select username from comments")]
        self.assertEqual(users, ["ann"])


class Config(unittest.TestCase):
    def test_example_is_valid_and_mistakes_are_named(self):
        self.assertEqual(validate(EXAMPLE), [])
        bad = copy.deepcopy(EXAMPLE)
        bad["campaigns"][0].update(keyword="two words", follow_button="a very long button label here", public_replies=["SENT!"])
        del bad["campaigns"][0]["deliver"]
        errors = " | ".join(validate(bad))
        for want in ("keyword", "follow_button", "public_replies", "deliver"):
            self.assertIn(want, errors)


class Webhook(unittest.TestCase):
    def test_signature_and_event_parsing(self):
        body = json.dumps({"entry": [{"time": 1800000000, "changes": [{"field": "comments", "value": {"id": "c1", "text": "CLIP", "from": {"id": "9", "username": "sam"}, "media": {"id": "m1"}}}],
                                      "messaging": [{"sender": {"id": "9"}, "timestamp": 1800000000000, "message": {"mid": "x1", "text": "LINK"}},
                                                    {"sender": {"id": "100"}, "timestamp": 1800000000000, "message": {"mid": "x2", "text": "hi", "is_echo": True}}]}]}).encode()
        sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(signature_ok("secret", body, sig))
        self.assertFalse(signature_ok("secret", body + b" ", sig))
        got = list(events(json.loads(body)))
        self.assertEqual([k for k, _ in got], ["comment", "message"])
        self.assertEqual(got[0][1]["media_id"], "m1")
        self.assertEqual(got[1][1]["text"], "LINK")


if __name__ == "__main__":
    unittest.main()
