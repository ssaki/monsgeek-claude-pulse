#!/usr/bin/env python3
"""Exercise pulse.py with synthetic hook payloads in dry-run mode (no keyboard needed)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "pulse.py")


class PulseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, PULSE_DRY_RUN="1", PULSE_LOG="1",
                        PULSE_STATE_DIR=self.tmp.name,
                        PULSE_CONFIG="/nonexistent/config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def fire(self, event, session="s1", **extra):
        payload = {"hook_event_name": event, "session_id": session, **extra}
        r = subprocess.run([sys.executable, "-I", SCRIPT], input=json.dumps(payload),
                           capture_output=True, text=True, env=self.env, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "", "hook must not write to stdout")
        return r.stderr.strip()

    def test_single_session_flow(self):
        self.assertIn("mode=1 ", self.fire("SessionStart"))
        self.assertIn("mode=16 ", self.fire("UserPromptSubmit"))
        self.assertEqual("", self.fire("PostToolUse", tool_name="Read"), "no change -> no HID write")
        self.assertIn("mode=2 ", self.fire("Notification", notification_type="permission_prompt"))
        self.assertIn("mode=16 ", self.fire("PostToolUse", tool_name="Bash"))
        self.assertIn("mode=1 ", self.fire("PreToolUse", tool_name="AskUserQuestion"))
        self.assertIn("mode=16 ", self.fire("PostToolUse", tool_name="AskUserQuestion"))
        self.assertIn("mode=1 ", self.fire("Stop"))
        self.assertIn("mode=4 ", self.fire("SessionEnd"))

    def test_most_urgent_session_wins(self):
        self.fire("UserPromptSubmit", session="a")
        self.assertIn("mode=2 ", self.fire("Notification", session="b"))
        # session a finishing its turn must not downgrade b's pending approval
        self.assertEqual("", self.fire("Stop", session="a"))
        # b resolved -> a's waiting state shows
        self.assertIn("mode=1 ", self.fire("PostToolUse", session="b"))
        self.fire("SessionEnd", session="b")
        self.assertIn("mode=4 ", self.fire("SessionEnd", session="a"))

    def test_unknown_event_ignored(self):
        self.assertEqual("", self.fire("SubagentStop"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "last-applied")))

    def test_garbage_input_does_not_fail(self):
        r = subprocess.run([sys.executable, "-I", SCRIPT], input="not json",
                           capture_output=True, text=True, env=self.env, timeout=10)
        self.assertEqual(r.returncode, 0)

    def test_config_override(self):
        cfg = os.path.join(self.tmp.name, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"states": {"busy": ["neon", 2, 1], "idle": ["constant", 2, 0, 0, 255, 0, "dazzle"]}}, f)
        self.env["PULSE_CONFIG"] = cfg
        self.assertIn("mode=3 brightness=2 speed=1 rgb=255,255,255 dazzle=False", self.fire("UserPromptSubmit"))
        self.assertIn("mode=1 brightness=2 speed=0 rgb=0,255,0 dazzle=True", self.fire("SessionEnd"))

    def test_report_bytes(self):
        sys.path.insert(0, os.path.dirname(SCRIPT))
        import pulse
        rep = pulse.led_report(2, 4, 0, 0, 255, 0, True)
        self.assertEqual(len(rep), 65)
        self.assertEqual(rep[:10].hex(), "000702040407 00ff00e8".replace(" ", ""))
        self.assertEqual(pulse.parse_spec(["Breathing", 4, 3, 255, 110, 0]), (2, 4, 3, 255, 110, 0, False))
        self.assertEqual(pulse.parse_spec(["16", 4, 3]), (16, 4, 3, 255, 255, 255, False))


class PulsectlTest(unittest.TestCase):
    def test_parse_led_response_roundtrip(self):
        sys.path.insert(0, os.path.dirname(SCRIPT))
        import pulse
        # what the keyboard echoes back for GET_LEDPARAM: cmd, mode, speed(inverted), brightness, option, r, g, b
        led = pulse.parse_led_response(bytes([0x87, 7, 2, 4, 8, 37, 111, 97]))
        self.assertEqual(pulse.spec_from(led), ["snake", 4, 2, 37, 111, 97])
        led = pulse.parse_led_response(bytes([0x87, 1, 4, 2, 7, 0, 255, 0]))
        self.assertEqual(pulse.spec_from(led), ["constant", 2, 0, 0, 255, 0, "dazzle"])
        # a spec read from the keyboard must re-encode to the same bytes pulse.py would send
        self.assertEqual(pulse.led_report(*pulse.parse_spec(pulse.spec_from(led)))[1:9],
                         bytes([0x07, 1, 4, 2, 7, 0, 255, 0]))

    def test_snapshot_skips_our_own_states(self):
        sys.path.insert(0, os.path.dirname(SCRIPT))
        import pulse
        states = dict(pulse.DEFAULT_STATES)
        with tempfile.TemporaryDirectory() as d:
            # keyboard shows our "waiting" colour -> hooks died earlier, do not snapshot it
            pulse.read_led = lambda node: ["constant", 4, 0, 255, 200, 0]
            self.assertIsNone(pulse.take_snapshot(d, "/dev/null", states))
            self.assertIsNone(pulse.pop_snapshot(d))
            # keyboard shows the user's own lighting -> snapshot, then pop restores it once
            pulse.read_led = lambda node: ["wave", 3, 1, 1, 2, 3, "dazzle"]
            self.assertEqual(pulse.take_snapshot(d, "/dev/null", states), ["wave", 3, 1, 1, 2, 3, "dazzle"])
            self.assertIsNone(pulse.take_snapshot(d, "/dev/null", states), "second takeover keeps the first snapshot")
            self.assertEqual(pulse.pop_snapshot(d), ["wave", 3, 1, 1, 2, 3, "dazzle"])
            self.assertIsNone(pulse.pop_snapshot(d))

    def test_set_and_reset_dry_run(self):
        tmp = tempfile.TemporaryDirectory()
        env = dict(os.environ, PULSE_DRY_RUN="1", PULSE_STATE_DIR=tmp.name, PULSE_CONFIG="/nonexistent")
        ctl = os.path.join(os.path.dirname(SCRIPT), "pulsectl.py")
        r = subprocess.run([sys.executable, "-I", ctl, "set", "approval"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mode=2 ", r.stderr)
        r = subprocess.run([sys.executable, "-I", ctl, "set", "bogus"], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        r = subprocess.run([sys.executable, "-I", ctl, "reset"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mode=4 ", r.stderr)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
