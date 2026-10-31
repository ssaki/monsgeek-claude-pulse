#!/usr/bin/env python3
"""Exercise pulse.py and pulsectl.py with synthetic hook payloads in dry-run mode (no keyboard needed)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
SCRIPT = os.path.join(SCRIPTS, "pulse.py")
CTL = os.path.join(SCRIPTS, "pulsectl.py")
sys.path.insert(0, SCRIPTS)
import pulse  # noqa: E402

# default spec modes, as printed by the dry run
APPROVAL, REVIEW, BUSY, OPEN, IDLE = "mode=2 ", "mode=1 brightness=4", "mode=16 ", "mode=1 brightness=2", "mode=4 "


class PulseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = os.path.join(self.tmp.name, "project")  # not a git repo: keyed by path
        os.makedirs(self.project)
        self.env = dict(os.environ, PULSE_DRY_RUN="1", PULSE_LOG="1",
                        PULSE_STATE_DIR=self.tmp.name,
                        PULSE_CONFIG="/nonexistent/config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def fire(self, event, session="s1", **extra):
        payload = {"hook_event_name": event, "session_id": session, "cwd": self.project, **extra}
        r = subprocess.run([sys.executable, "-I", SCRIPT], input=json.dumps(payload),
                           capture_output=True, text=True, env=self.env, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "", "hook must not write to stdout")
        return r.stderr.strip()

    def ctl(self, *args, ok=True):
        r = subprocess.run([sys.executable, "-I", CTL, *args], capture_output=True, text=True,
                           env=self.env, timeout=10, cwd=self.project)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_single_session_flow(self):
        self.assertIn(OPEN, self.fire("SessionStart"), "session open, nothing pending")
        self.assertIn(BUSY, self.fire("UserPromptSubmit"))
        self.assertEqual("", self.fire("PostToolUse", tool_name="Read"), "no change -> no HID write")
        self.assertIn(APPROVAL, self.fire("PermissionRequest", tool_name="Write"))
        self.assertIn(BUSY, self.fire("PostToolUse", tool_name="Write"))
        self.assertIn(APPROVAL, self.fire("Notification", notification_type="permission_prompt"))
        self.assertIn(BUSY, self.fire("PostToolUse", tool_name="Bash"))
        self.assertIn(REVIEW, self.fire("PreToolUse", tool_name="AskUserQuestion"))
        self.assertIn(BUSY, self.fire("PostToolUse", tool_name="AskUserQuestion"))
        self.assertIn(OPEN, self.fire("Stop"), "finished turn, no hint -> open")
        self.assertIn(IDLE, self.fire("SessionEnd"))

    def test_stage_hint_drives_review(self):
        self.fire("UserPromptSubmit")
        self.ctl("stage", "review", "spec drafted, needs comments")
        self.assertIn(REVIEW, self.fire("Stop"), "agent flagged the stage -> review")
        self.assertIn(BUSY, self.fire("UserPromptSubmit"))
        self.assertIn(REVIEW, self.fire("Stop"), "hint persists across turns until cleared")
        self.fire("UserPromptSubmit")  # s1 busy again
        self.assertIn(REVIEW, self.fire("SessionStart", session="s2"),
                      "a new session on a pending project shows review, outranking busy")
        self.ctl("stage", "clear")
        self.assertIn(BUSY, self.fire("UserPromptSubmit", session="s2"))
        self.assertEqual("", self.fire("Stop", session="s2"), "s1 still busy")
        self.assertIn(OPEN, self.fire("Stop"), "hint cleared -> open")
        self.assertEqual("", self.fire("SessionEnd", session="s2"))
        self.assertIn(IDLE, self.fire("SessionEnd"))
        # other projects are unaffected
        self.ctl("stage", "review")
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(other)
        self.fire("UserPromptSubmit", cwd=other)
        self.assertIn(OPEN, self.fire("Stop", cwd=other))

    def test_stage_dotfile_override_and_staleness(self):
        with open(os.path.join(self.project, ".pulse-stage"), "w") as f:
            f.write("review plan ready\nmore text ignored\n")
        self.fire("UserPromptSubmit")
        self.assertIn(REVIEW, self.fire("Stop"))
        out = self.ctl("stage").stdout
        self.assertIn('review  "plan ready"', out)
        # a day-old hint no longer nags
        old = pulse.time.time() - pulse.STAGE_STALE_SECONDS - 10
        os.utime(os.path.join(self.project, ".pulse-stage"), (old, old))
        self.fire("UserPromptSubmit")
        self.assertIn(OPEN, self.fire("Stop"))
        # garbage never breaks anything
        with open(os.path.join(self.project, ".pulse-stage"), "w") as f:
            f.write("\x00\xff nonsense")
        self.fire("UserPromptSubmit")
        self.assertIn(OPEN, self.fire("Stop"))

    def test_on_stop_config(self):
        cfg = os.path.join(self.tmp.name, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"on_stop": "review"}, f)
        self.env["PULSE_CONFIG"] = cfg
        self.fire("UserPromptSubmit")
        self.assertIn(REVIEW, self.fire("Stop"), "on_stop=review -> solid colour until next prompt")
        with open(cfg, "w") as f:
            json.dump({"on_stop": "idle"}, f)
        self.fire("UserPromptSubmit")
        self.assertIn(IDLE, self.fire("Stop"), "on_stop=idle -> normal lighting after every turn")

    def test_most_urgent_session_wins(self):
        self.fire("UserPromptSubmit", session="a")
        self.assertIn(APPROVAL, self.fire("PermissionRequest", session="b"))
        # session a finishing its turn must not downgrade b's pending approval
        self.assertEqual("", self.fire("Stop", session="a"))
        # b resolved and busy again
        self.assertIn(BUSY, self.fire("PostToolUse", session="b"))
        # b finishes -> both open -> dim
        self.assertIn(OPEN, self.fire("Stop", session="b"))
        self.assertEqual("", self.fire("SessionEnd", session="b"), "a is still open -> no change")
        self.assertIn(IDLE, self.fire("SessionEnd", session="a"))

    def test_unknown_event_ignored(self):
        self.assertEqual("", self.fire("SubagentStop"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "last-applied")))

    def test_garbage_input_does_not_fail(self):
        r = subprocess.run([sys.executable, "-I", SCRIPT], input="not json",
                           capture_output=True, text=True, env=self.env, timeout=10)
        self.assertEqual(r.returncode, 0)

    def test_config_override_and_alias(self):
        cfg = os.path.join(self.tmp.name, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"states": {"busy": ["neon", 2, 1],
                                  "waiting": ["constant", 3, 0, 9, 9, 9],
                                  "idle": ["constant", 2, 0, 0, 255, 0, "dazzle"]}}, f)
        self.env["PULSE_CONFIG"] = cfg
        self.assertIn("mode=3 brightness=2 speed=1 rgb=255,255,255 dazzle=False", self.fire("UserPromptSubmit"))
        self.assertIn("rgb=9,9,9", self.fire("PreToolUse", tool_name="AskUserQuestion"), "old 'waiting' key maps to review")
        self.assertIn("mode=1 brightness=2 speed=0 rgb=0,255,0 dazzle=True", self.fire("SessionEnd"))

    def test_report_bytes(self):
        rep = pulse.led_report(2, 4, 0, 0, 255, 0, True)
        self.assertEqual(len(rep), 65)
        self.assertEqual(rep[:10].hex(), "00070204040700ff00e8")
        self.assertEqual(pulse.parse_spec(["Breathing", 4, 3, 255, 110, 0]), (2, 4, 3, 255, 110, 0, False))
        self.assertEqual(pulse.parse_spec(["16", 4, 3]), (16, 4, 3, 255, 255, 255, False))

    def test_parse_led_response_roundtrip(self):
        led = pulse.parse_led_response(bytes([0x87, 7, 2, 4, 8, 37, 111, 97]))
        self.assertEqual(pulse.spec_from(led), ["snake", 4, 2, 37, 111, 97])
        led = pulse.parse_led_response(bytes([0x87, 1, 4, 2, 7, 0, 255, 0]))
        self.assertEqual(pulse.spec_from(led), ["constant", 2, 0, 0, 255, 0, "dazzle"])
        self.assertEqual(pulse.led_report(*pulse.parse_spec(pulse.spec_from(led)))[1:9],
                         bytes([0x07, 1, 4, 2, 7, 0, 255, 0]))

    def test_snapshot_skips_our_own_states(self):
        states = dict(pulse.DEFAULT_STATES)
        with tempfile.TemporaryDirectory() as d:
            pulse.read_led = lambda node: ["constant", 4, 0, 255, 120, 0]  # our "review" colour
            self.assertIsNone(pulse.take_snapshot(d, "/dev/null", states))
            self.assertIsNone(pulse.pop_snapshot(d))
            pulse.read_led = lambda node: ["wave", 3, 1, 1, 2, 3, "dazzle"]
            self.assertEqual(pulse.take_snapshot(d, "/dev/null", states), ["wave", 3, 1, 1, 2, 3, "dazzle"])
            self.assertIsNone(pulse.take_snapshot(d, "/dev/null", states), "second takeover keeps the first snapshot")
            self.assertEqual(pulse.pop_snapshot(d), ["wave", 3, 1, 1, 2, 3, "dazzle"])
            self.assertIsNone(pulse.pop_snapshot(d))

    def test_pulsectl_set_reset_and_bad_state(self):
        self.assertIn(APPROVAL, self.ctl("set", "approval").stderr)
        self.assertIn(REVIEW, self.ctl("set", "waiting").stderr, "alias accepted")
        self.assertNotEqual(self.ctl("set", "bogus", ok=False).returncode, 0)
        self.assertIn(IDLE, self.ctl("reset").stderr)
        self.assertNotEqual(self.ctl("stage", "bogus", ok=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
