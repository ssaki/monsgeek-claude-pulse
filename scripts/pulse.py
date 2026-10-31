#!/usr/bin/env python3
"""monsgeek-claude-pulse: map Claude Code hook events to keyboard LED states.

Reads the hook payload (JSON) from stdin, records this session's state,
computes the most urgent state across all live Claude Code sessions, and
applies it to the keyboard only when it actually changed.

States, by what they ask of you, most urgent first:
  approval  blocking permission prompt                    -> breathing orange
  review    a stage concluded, progress needs your input  -> solid orange
  busy      Claude is working                             -> rainbow
  open      a session is live, Claude is done, nothing    -> dim blue
            pending: the subtle "you were doing something"
  idle      no live sessions                              -> your normal lighting

"review" comes from a stage hint the agent leaves for the project (see
pulsectl.py stage and the pulse-stage skill), or from AskUserQuestion.
A missing or stale hint means "open". Nothing here ever throws on it.

Unless "restore_previous" is false in the config, the lighting found on the
keyboard when the first session takes over is snapshotted and put back when the
last session ends, instead of the fixed idle spec (which is then only a fallback).

The LED command is written straight to the keyboard's vendor HID interface
(SET_LEDPARAM 0x07, as documented in monsgeek-akko-linux/docs/PROTOCOL.md),
which also lets us set the "dazzle" flag that `iot_driver set-led` cannot.
On Linux that is the hidraw node; if none is found, falls back to
`iot_driver set-led`. On Windows it is a HID feature report through hid.dll
(see pulse_windows.py); no driver is needed there.

Environment:
  PULSE_DRY_RUN=1   print what would be sent instead of touching the keyboard
  PULSE_LOG=1       append a trace to $STATE_DIR/pulse.log
  PULSE_CONFIG      path to a JSON file overriding states / options
  PULSE_STATE_DIR   override the state directory (used by tests)

Never blocks Claude: every failure is swallowed and exit code is 0.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import msvcrt
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pulse_windows as win
else:
    import fcntl
    import glob

# State spec: [mode, brightness 0-4, speed 0-4, r, g, b, "dazzle"?]
# Mode is a name or number 0-25. A trailing "dazzle" sets the firmware's
# colour-cycle / rainbow-gradient flag (its look depends on the mode).
DEFAULT_STATES = {
    "approval": ["breathing", 4, 3, 255, 110, 0],
    "review":   ["constant", 4, 0, 255, 120, 0],
    "busy":     ["rainbow", 4, 3],
    "open":     ["constant", 2, 0, 0, 120, 255],
    "idle":     ["wave", 4, 2],
}
STATE_ALIASES = {"waiting": "review"}  # pre-0.2 config key
PRIORITY = {"approval": 4, "review": 3, "busy": 2, "open": 1}
STALE_SECONDS = 12 * 3600        # drop session files this old even if the pid check passes
STAGE_STALE_SECONDS = 24 * 3600  # a forgotten "review" hint stops nagging after this
STAGE_FILE = ".pulse-stage"      # optional per-project override, first line: review|open [note]

EVENT_STATE = {
    "SessionStart": "stage",      # open, or review if the project's stage hint says so
    "UserPromptSubmit": "busy",
    "PostToolUse": "busy",
    "Stop": "stage",              # same, with config "on_stop" as the default
    "Notification": "approval",   # hooks.json only routes permission_prompt here
    "PermissionRequest": "approval",
    "SessionEnd": None,           # removes the session
}
# What a finished turn looks like when no stage hint says "review".
ON_STOP_CHOICES = ("open", "review", "idle")
ON_STOP = "open"

LED_MODES = [
    "off", "constant", "breathing", "neon", "wave", "ripple", "raindrop", "snake",
    "reactive", "converge", "sine wave", "kaleidoscope", "line wave", "user picture",
    "laser", "circle wave", "rainbow", "rain down", "meteor", "reactive off",
    "music patterns", "screen sync", "music bars", "train", "fireworks", "per-key color",
]
VENDOR_ID = "00003151"
SET_LEDPARAM, GET_LEDPARAM = 0x07, 0x87
DAZZLE_ON, DAZZLE_OFF = 7, 8
HIDIOCSFEATURE = 0xC0000000 | (65 << 16) | (ord("H") << 8) | 0x06
HIDIOCGFEATURE = 0xC0000000 | (65 << 16) | (ord("H") << 8) | 0x07
SNAPSHOT = "previous-led.json"


def state_dir():
    d = os.environ.get("PULSE_STATE_DIR")
    if not d:
        base = win.state_base() if WINDOWS else (os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
        d = os.path.join(base, "monsgeek-claude-pulse")
    os.makedirs(d, exist_ok=True)
    return d


def log(d, msg):
    if os.environ.get("PULSE_LOG"):
        with open(os.path.join(d, "pulse.log"), "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def config_path():
    if os.environ.get("PULSE_CONFIG"):
        return os.environ["PULSE_CONFIG"]
    if WINDOWS:
        return os.path.join(win.config_dir(), "config.json")
    return os.path.expanduser("~/.config/monsgeek-claude-pulse/config.json")


def load_config():
    """-> (states, iot_driver path or None, restore_previous flag)
    Also applies the "on_stop" choice to the module-level ON_STOP."""
    global ON_STOP
    states = dict(DEFAULT_STATES)
    driver = None
    restore = True
    try:
        with open(config_path()) as f:
            cfg = json.load(f)
        for k, v in cfg.get("states", {}).items():
            k = STATE_ALIASES.get(k, k)
            if k in states and isinstance(v, list):
                states[k] = v
        driver = cfg.get("iot_driver")
        restore = bool(cfg.get("restore_previous", True))
        if cfg.get("on_stop") in ON_STOP_CHOICES:
            ON_STOP = cfg["on_stop"]
    except (OSError, ValueError):
        pass
    return states, driver, restore


def parse_spec(spec):
    """-> (mode, brightness, speed, r, g, b, dazzle)"""
    spec = list(spec)
    dazzle = False
    if spec and isinstance(spec[-1], str) and spec[-1].lower() == "dazzle":
        dazzle = True
        spec.pop()
    mode = spec[0] if spec else "constant"
    if isinstance(mode, str) and not mode.lstrip("-").isdigit():
        mode = LED_MODES.index(mode.lower().replace("_", " ")) if mode.lower().replace("_", " ") in LED_MODES else 1
    nums = [int(x) for x in spec[1:]]
    nums += [4, 2, 255, 255, 255][len(nums):]  # same defaults as `iot_driver set-led`
    b, s, r, g, bl = nums[:5]
    return int(mode), min(b, 4), min(s, 4), r & 255, g & 255, bl & 255, dazzle


# ---------------------------------------------------------------- keyboard

def find_device():
    """The keyboard's vendor-config interface: VID 0x3151, usage page 0xFFFF.
    A /dev/hidrawN node on Linux, a \\\\?\\hid#... device path on Windows."""
    if WINDOWS:
        return win.find_device()
    for sysdev in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(sysdev, "device", "uevent")) as f:
                if f"HID_ID=0003:{VENDOR_ID}:" not in f.read():
                    continue
            with open(os.path.join(sysdev, "device", "report_descriptor"), "rb") as f:
                if not f.read(3) == b"\x06\xff\xff":
                    continue
        except OSError:
            continue
        node = "/dev/" + os.path.basename(sysdev)
        if os.access(node, os.W_OK):
            return node
    return None


def led_report(mode, brightness, speed, r, g, b, dazzle):
    data = [SET_LEDPARAM, mode, 4 - speed, brightness, DAZZLE_ON if dazzle else DAZZLE_OFF, r, g, b]
    data.append(255 - (sum(data) & 0xFF))  # "Bit8" checksum
    return bytes([0] + data + [0] * (64 - len(data)))


def _set_feature(node, report):
    if WINDOWS:
        win.set_feature(node, report)
        return
    with open(node, "r+b", buffering=0) as f:
        fcntl.ioctl(f, HIDIOCSFEATURE, report)


def _get_feature(node):
    if WINDOWS:
        return win.get_feature(node, 65)
    buf = bytearray(65)
    with open(node, "r+b", buffering=0) as f:
        fcntl.ioctl(f, HIDIOCGFEATURE, buf)
    return bytes(buf)


def send_led(node, report):
    _set_feature(node, report)


def parse_led_response(data):
    """GET_LEDPARAM echo: cmd, mode, speed(inverted), brightness, option, r, g, b"""
    return {
        "mode": data[1], "speed": 4 - min(data[2], 4), "brightness": data[3],
        "dazzle": (data[4] & 0x0F) == DAZZLE_ON,
        "r": data[5], "g": data[6], "b": data[7],
    }


def spec_from(led):
    name = LED_MODES[led["mode"]] if led["mode"] < len(LED_MODES) else led["mode"]
    spec = [name, led["brightness"], led["speed"], led["r"], led["g"], led["b"]]
    if led["dazzle"]:
        spec.append("dazzle")
    return spec


def read_led(node):
    """Current LED settings as a config spec, read from the keyboard."""
    query = [GET_LEDPARAM, 0, 0, 0, 0, 0, 0]
    query.append(255 - (sum(query) & 0xFF))  # "Bit7" checksum
    report = bytes([0] + query + [0] * (64 - len(query)))
    for _ in range(3):  # hidraw may hand back a stale buffered response first
        _set_feature(node, report)
        time.sleep(0.1)
        data = _get_feature(node)[1:]
        if data[0] == GET_LEDPARAM:
            return spec_from(parse_led_response(data))
    raise RuntimeError("keyboard did not answer GET_LEDPARAM")


# ---------------------------------------------------------------- snapshot

def snapshot_path(d):
    return os.path.join(d, SNAPSHOT)


def take_snapshot(d, node, states):
    """Remember the keyboard's lighting before we change it, unless it already
    shows one of our own states (hooks died mid-session and left it there)."""
    if os.path.exists(snapshot_path(d)):
        return None
    spec = read_led(node)
    ours = {tuple(parse_spec(v)) for k, v in states.items() if k != "idle"}
    if tuple(parse_spec(spec)) in ours:
        return None
    with open(snapshot_path(d), "w") as f:
        json.dump(spec, f)
    return spec


def pop_snapshot(d):
    try:
        with open(snapshot_path(d)) as f:
            spec = json.load(f)
        os.remove(snapshot_path(d))
        return spec
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- stage hint

def project_root(cwd):
    """Git top level of cwd, else cwd itself: the key a stage hint is filed under."""
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.abspath(cwd)


def stage_path(d, root):
    return os.path.join(d, "stage-" + hashlib.sha1(root.encode()).hexdigest()[:12] + ".json")


def read_stage(d, cwd):
    """-> {"stage", "note", "root", "t", "source"} or None. Never raises."""
    root = project_root(cwd)
    dotfile = os.path.join(root, STAGE_FILE)
    try:
        with open(dotfile) as f:
            word, _, note = f.readline().strip().partition(" ")
        if word.lower() in ("review", "open"):
            return {"stage": word.lower(), "note": note.strip(), "root": root,
                    "t": os.path.getmtime(dotfile), "source": dotfile}
    except OSError:
        pass
    try:
        with open(stage_path(d, root)) as f:
            rec = json.load(f)
        if rec.get("stage") in ("review", "open"):
            rec.setdefault("root", root)
            rec["source"] = "pulsectl"
            return rec
    except (OSError, ValueError):
        pass
    return None


def write_stage(d, root, stage, note=""):
    path = stage_path(d, root)
    if stage == "clear":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    with open(path, "w") as f:
        json.dump({"stage": stage, "note": note, "root": root, "t": time.time()}, f)


def stage_state(d, cwd, default):
    rec = read_stage(d, cwd)
    if rec and rec["stage"] == "review" and time.time() - rec.get("t", 0) < STAGE_STALE_SECONDS:
        return "review"
    return default


# ---------------------------------------------------------------- sessions

_PROCS = None


def _procs():
    """Windows only: {pid: (ppid, exe)} snapshot, taken once per hook run."""
    global _PROCS
    if _PROCS is None:
        _PROCS = win.process_table()
    return _PROCS


def claude_pid():
    """Walk up the process tree to the Claude Code process that spawned us."""
    pid = os.getppid()
    if WINDOWS:
        table = _procs()
        for _ in range(8):
            ppid, exe = table.get(pid, (0, ""))
            if exe.lower() in ("claude.exe", "node.exe"):
                return pid
            if not ppid or ppid == pid:
                return None
            pid = ppid
        return None
    for _ in range(8):
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)
            comm = fields[0].split("(", 1)[1]
            ppid = int(fields[1].split()[1])
        except (OSError, IndexError, ValueError):
            return None
        if comm in ("claude", "node"):
            return pid
        if ppid <= 1:
            return None
        pid = ppid
    return None


def pid_alive(pid):
    if not pid:
        return True  # unknown pid: assume alive, rely on STALE_SECONDS
    if WINDOWS:
        return pid in _procs()
    return os.path.exists(f"/proc/{pid}")


def event_to_state(payload, d):
    event = payload.get("hook_event_name", "")
    if event == "PreToolUse":
        return "review" if payload.get("tool_name") == "AskUserQuestion" else "busy"
    state = EVENT_STATE.get(event, "ignore")
    if state == "stage":
        default = ON_STOP if event == "Stop" else "open"
        return stage_state(d, payload.get("cwd") or os.getcwd(), default)
    return state


def write_session(d, session, state):
    path = os.path.join(d, f"session-{session}.json")
    if state is None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"state": state, "pid": claude_pid(), "t": time.time()}, f)
    os.replace(tmp, path)


def global_state(d):
    best, best_p = "idle", 0
    now = time.time()
    for name in os.listdir(d):
        if not name.startswith("session-") or not name.endswith(".json"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if now - rec.get("t", 0) > STALE_SECONDS or not pid_alive(rec.get("pid")):
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        p = PRIORITY.get(rec.get("state"), 0)
        if p > best_p:
            best, best_p = rec["state"], p
    return best


def apply(d, state, states, driver, restore=False):
    last_path = os.path.join(d, "last-applied")
    try:
        with open(last_path) as f:
            if f.read().strip() == state:
                return False
    except OSError:
        pass
    node = find_device()
    dry = bool(os.environ.get("PULSE_DRY_RUN"))
    spec = states[state]
    if restore and node and not dry:
        if state == "idle":
            spec = pop_snapshot(d) or spec
        else:
            taken = take_snapshot(d, node, states)
            if taken:
                log(d, f"snapshot {taken}")
    mode, bright, speed, r, g, b, dazzle = parse_spec(spec)
    desc = f"led mode={mode} brightness={bright} speed={speed} rgb={r},{g},{b} dazzle={dazzle}"
    if dry:
        print(f"DRY_RUN {desc} via {node or 'iot_driver'}", file=sys.stderr)
    elif node:
        send_led(node, led_report(mode, bright, speed, r, g, b, dazzle))
    elif WINDOWS:
        log(d, "no keyboard found (vendor 3151, usage page ffff); nothing sent")
        return False
    else:
        exe = driver or shutil.which("iot_driver") or "/usr/local/bin/iot_driver"
        subprocess.run([exe, "set-led", *map(str, (mode, bright, speed, r, g, b))],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    with open(last_path, "w") as f:
        f.write(state)
    return True


def main():
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    d = state_dir()
    session = str(payload.get("session_id") or "nosession").replace("/", "_")
    states, driver, restore = load_config()  # before event_to_state: may set ON_STOP
    state = event_to_state(payload, d)
    if state == "ignore":
        return
    with open(os.path.join(d, ".lock"), "w") as lock:
        if WINDOWS:
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)  # retries for ~10 s, then raises
        else:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            write_session(d, session, state)
            g = global_state(d)
            changed = apply(d, g, states, driver, restore)
        finally:
            if WINDOWS:
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    log(d, f"{payload.get('hook_event_name')} session={session[:8]} -> {state} global={g} applied={changed}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never block Claude Code
        try:
            log(state_dir(), f"error: {e!r}")
        except Exception:
            pass
    sys.exit(0)
