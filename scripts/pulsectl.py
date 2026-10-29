#!/usr/bin/env python3
"""Manual control for monsgeek-claude-pulse, for when the hooks go missing
(force-quit agent or IDE, crashed terminal, ...).

  pulsectl.py set <state>      apply a state: approval, waiting, busy, idle
  pulsectl.py reset            forget all sessions and apply idle (restores the
                               snapshot if restore_previous is on)
  pulsectl.py revert           put back the lighting snapshotted when the plugin took over
  pulsectl.py status           show tracked sessions, last applied state, keyboard readback
  pulsectl.py scan [--save S]  print the keyboard's current LED settings as a config spec,
                               optionally saving it as state S in the config file
  pulsectl.py list             print the configured states

Honours PULSE_CONFIG, PULSE_STATE_DIR and PULSE_DRY_RUN like pulse.py.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pulse  # noqa: E402

CONFIG_PATH = os.environ.get("PULSE_CONFIG") or os.path.expanduser(
    "~/.config/monsgeek-claude-pulse/config.json")


def apply_state(state):
    states, driver, restore = pulse.load_config()
    if state not in states:
        sys.exit(f"unknown state {state!r}; choose from {', '.join(states)}")
    d = pulse.state_dir()
    try:
        os.remove(os.path.join(d, "last-applied"))  # force a write even if it matches
    except FileNotFoundError:
        pass
    pulse.apply(d, state, states, driver, restore)
    print(f"applied {state}: {states[state]}")


def cmd_revert():
    d = pulse.state_dir()
    spec = pulse.pop_snapshot(d)
    if not spec:
        sys.exit("no snapshot of previous lighting (restore_previous off, or nothing taken over yet)")
    node = pulse.find_hidraw()
    if not node:
        sys.exit("no writable hidraw node for the keyboard")
    pulse.send_led(node, pulse.led_report(*pulse.parse_spec(spec)))
    try:
        os.remove(os.path.join(d, "last-applied"))
    except FileNotFoundError:
        pass
    print(f"restored previous lighting: {spec}")


def cmd_reset():
    d = pulse.state_dir()
    n = 0
    for name in os.listdir(d):
        if name.startswith("session-"):
            os.remove(os.path.join(d, name))
            n += 1
    print(f"cleared {n} session file(s)")
    apply_state("idle")


def cmd_status():
    d = pulse.state_dir()
    print(f"state dir:    {d}")
    try:
        with open(os.path.join(d, "last-applied")) as f:
            print(f"last applied: {f.read().strip()}")
    except OSError:
        print("last applied: (none)")
    for name in sorted(os.listdir(d)):
        if name.startswith("session-") and name.endswith(".json"):
            with open(os.path.join(d, name)) as f:
                rec = json.load(f)
            alive = pulse.pid_alive(rec.get("pid"))
            age = int(time.time() - rec.get("t", 0))
            print(f"  {name[8:16]}  {rec.get('state'):9} pid={rec.get('pid')} "
                  f"{'alive' if alive else 'DEAD'}  {age}s ago")
    try:
        with open(pulse.snapshot_path(d)) as f:
            print(f"snapshot:     {json.load(f)}  (lighting before takeover)")
    except (OSError, ValueError):
        print("snapshot:     (none)")
    node = pulse.find_hidraw()
    print(f"hidraw node:  {node or '(not found)'}")
    if node:
        print(f"keyboard:     {pulse.read_led(node)}")


def cmd_scan(save=None):
    node = pulse.find_hidraw()
    if not node:
        sys.exit("no writable hidraw node for the keyboard; is the udev rule installed?")
    spec = pulse.read_led(node)
    print(json.dumps(spec))
    if save:
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        cfg.setdefault("states", {})[save] = spec
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        print(f"saved as {save!r} in {CONFIG_PATH}")


def cmd_list():
    states, _, restore = pulse.load_config()
    for k, v in states.items():
        print(f"{k:9} {v}")
    print(f"restore_previous: {restore}" + ("  (idle spec is the fallback)" if restore else ""))


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    cmd, args = argv[0], argv[1:]
    if cmd == "set" and len(args) == 1:
        apply_state(args[0])
    elif cmd == "reset":
        cmd_reset()
    elif cmd == "revert":
        cmd_revert()
    elif cmd == "status":
        cmd_status()
    elif cmd == "scan":
        save = args[1] if len(args) == 2 and args[0] == "--save" else None
        if args and not save:
            sys.exit("usage: pulsectl.py scan [--save <state>]")
        cmd_scan(save)
    elif cmd == "list":
        cmd_list()
    else:
        sys.exit(__doc__.strip())


if __name__ == "__main__":
    main(sys.argv[1:])
