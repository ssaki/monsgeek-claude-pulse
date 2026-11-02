# monsgeek-claude-pulse

Visual feedback for [Claude Code](https://code.claude.com) on a MonsGeek / Akko keyboard's RGB
lighting. The keyboard shows what the agent is doing, so you can look away from the terminal:

| What it asks of you | State | Default lighting |
|---|---|---|
| decide now: a permission prompt is blocking | approval | pulsing orange |
| make progress: a stage concluded, read / comment / approve | review | solid orange |
| nothing, Claude is working | busy | rainbow |
| nothing, but a session is open and you were doing something | open | dim blue |
| nothing, no live sessions | idle | whatever the keyboard showed before |

All five are configurable, and you can capture any lighting the keyboard is currently showing
with one command. The "review" state comes from the agent itself, through a hint it leaves for
the project (see [Stage hints](#stage-hints)), so the keyboard can tell "done for now" from
"done, and the ball is in your court".

## How it works

It is a Claude Code plugin made of [hooks](https://code.claude.com/docs/en/hooks). Each hook runs
`scripts/pulse.py`, which records the session's state, picks the most urgent state across all
running sessions, and only talks to the keyboard when that state changes.

The script writes the firmware's `SET_LEDPARAM` command straight to the keyboard's vendor HID
interface, following the protocol reverse-engineered by
[monsgeek-akko-linux](https://github.com/echtzeit-solutions/monsgeek-akko-linux) for the RongYuan
RY5088 / YC3121 firmware used in MonsGeek and Akko Hall-effect boards. On Linux that is the
hidraw node, and the driver is still needed for its udev rule (hidraw access without root); it is
also the easiest way to read and experiment with the lighting (`iot_driver led`, `iot_driver tui`).
On Windows it is a HID feature report through `hid.dll`, with nothing to install.

## Requirements

- A MonsGeek or Akko keyboard on RY5088 / YC3121 firmware (USB vendor ID `3151`), wired.
  Tested with a MonsGeek M1 V5 HE (`3151:5030`). Check the driver's device list for others.
- Linux, or Windows with Claude Code running on the host (not in WSL, which cannot see USB HID).
  See [docs/windows.md](docs/windows.md) for Windows.
- Python 3.8 or newer. No third-party packages.
- Claude Code 2.1 or newer.
- On Linux, the `monsgeek-akko-linux` driver, for its udev rule. See [docs/setup.md](docs/setup.md).

## Setup

Linux walkthrough in [docs/setup.md](docs/setup.md), Windows in [docs/windows.md](docs/windows.md).
Short Linux version, from the repository root:

```bash
# 1. Driver and udev rule (one time; Debian/Ubuntu package names, see upstream README for others)
sudo apt install build-essential pkg-config libudev-dev libhidapi-dev protobuf-compiler \
     libssl-dev libpulse-dev libpipewire-0.3-dev libclang-dev
git clone https://github.com/echtzeit-solutions/monsgeek-akko-linux vendor/monsgeek-akko-linux
make -C vendor/monsgeek-akko-linux driver
sudo make -C vendor/monsgeek-akko-linux install-driver install-udev
# unplug and replug the keyboard, then:
iot_driver led

# 2. Try the plugin
claude --plugin-dir .

# 3. Make it permanent
claude plugin marketplace add "$PWD"
claude plugin install monsgeek-claude-pulse@monsgeek-claude-pulse
```

## Configuration

Optional file `~/.config/monsgeek-claude-pulse/config.json` (`%APPDATA%\monsgeek-claude-pulse\config.json` on Windows). Each state is
`[mode, brightness 0-4, speed 0-4, r, g, b]`, the same order as `iot_driver set-led`, plus an
optional trailing `"dazzle"`. Mode names and numbers are in the driver's
[CLI reference](https://github.com/echtzeit-solutions/monsgeek-akko-linux/blob/master/docs/CLI.md).

```json
{
  "restore_previous": true,
  "on_stop": "open",
  "states": {
    "approval": ["breathing", 4, 4, 255, 50, 0],
    "review":   ["constant", 4, 0, 255, 50, 0],
    "busy":     ["snake", 4, 2, 37, 111, 97],
    "open":     ["constant", 2, 4, 26, 220, 255],
    "idle":     ["constant", 2, 0, 0, 255, 0, "dazzle"]
  }
}
```

- `restore_previous` (default `true`): snapshot whatever the keyboard is showing when the first
  session takes over, and put it back when the last session ends. `idle` is then only a fallback
  for when no snapshot exists. Set it to `false` to always end on the `idle` spec instead. If the keyboard is already showing one of the plugin's own states at takeover, because
  hooks died earlier and left it there, nothing is snapshotted.
- `on_stop` (default `"open"`): what a finished turn looks like when no stage hint says
  `review`. `"open"` is the subtle session colour, `"idle"` goes all the way back to normal
  lighting after every turn, `"review"` holds the attention colour until your next prompt.
- The pre-0.2 state name `waiting` is still accepted and means `review`.
- `"dazzle"` sets the firmware's colour-cycle flag. On `constant` it gives the static rainbow
  gradient these keyboards ship with. `iot_driver set-led` cannot set it, which is one reason the
  script talks to hidraw directly.
- Colours on these LEDs lean towards green, so an orange that looks right on screen can come out
  yellow on the keys. Tune by eye, then capture with `scan` below.

The easiest way to build a config is to set the lighting you want using the keyboard's Fn keys or
`iot_driver tui`, then run `scan --save <state>` for each state.

## Stage hints

The hooks can see that Claude stopped, but not whether the work is at a point that needs you.
The agent knows, so it tells the plugin. The repository ships a `pulse-stage` skill with the
vocabulary and the command:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pulsectl.py" stage review "spec drafted, needs your comments"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pulsectl.py" stage open "plan approved, implementing"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pulsectl.py" stage clear
```

(On Windows, `python` instead of `python3`.)

While a project's hint says `review`, every finished turn and every new session on that project
shows the review colour. It is keyed by the project's git root, stored in the plugin's state
directory, and expires after a day so a forgotten hint does not nag forever. Projects without a
hint behave as before.

The skill explains when to set it, but the trigger is up to you. Opt a project in with a line in
its `CLAUDE.md`, for example:

```markdown
When a spec, plan, or task stage concludes and needs my review before work can continue,
record it with the pulse-stage skill. Clear it when I have responded and work moves on.
```

If you would rather keep the hint inside the repository, a file named `.pulse-stage` in the
project root, first line `review` or `open` followed by an optional note, overrides the stored
hint. Remember to gitignore it.

## Manual control

If the hooks go missing, for example after a force-quit of the agent or the IDE, the keyboard
stays on whatever was last applied. `scripts/pulsectl.py` fixes that by hand:

```bash
python3 scripts/pulsectl.py reset            # forget all sessions, apply idle (or the snapshot)
python3 scripts/pulsectl.py revert           # put back the lighting snapshotted at takeover
python3 scripts/pulsectl.py set approval     # apply one state: approval, review, busy, open, idle
python3 scripts/pulsectl.py status           # tracked sessions, last applied, keyboard readback
python3 scripts/pulsectl.py scan             # print the keyboard's current lighting as a config spec
python3 scripts/pulsectl.py scan --save busy # ...and store it as that state in the config
python3 scripts/pulsectl.py list             # show the configured states
python3 scripts/pulsectl.py stage            # show this project's stage hint
```

A shell alias keeps it short:

```bash
alias pulsectl="python3 $PWD/scripts/pulsectl.py"
```

## Lighting after a restart

Hooks only run inside a Claude Code session, and the keyboard keeps the last command it was given
in its own flash. So if the machine restarts while a session is live, the plugin's colour is still
on the keys at the next logon, until the next session ends or you run `pulsectl.py reset`. Two
things make that automatic.

**1. Dump your default lighting into the config.** Put the keyboard on the lighting you want back
(its own keys, or `pulsectl.py revert` if a snapshot is held) and store it as the `idle` state:

```bash
python3 scripts/pulsectl.py scan --save idle
```

That spec is applied whenever no snapshot exists, which is the case after a Linux reboot (the
runtime directory holding the snapshot is cleared). On Windows the snapshot survives reboots and
takes precedence, so the saved `idle` is the fallback.

**2. Run `reset` at logon.** It forgets any sessions whose processes are gone and applies the
snapshot or the saved `idle`. How to register it is in [docs/setup.md](docs/setup.md#7-restore-the-lighting-at-login)
for Linux (a systemd user unit) and [docs/windows.md](docs/windows.md#restore-the-lighting-at-logon)
for Windows (a shortcut in the Startup folder; no administrator rights).

## Debugging

```bash
PULSE_DRY_RUN=1 PULSE_LOG=1 claude --plugin-dir .      # log what would be sent, touch nothing
cat "${XDG_RUNTIME_DIR:-/tmp}/monsgeek-claude-pulse/pulse.log"
python3 tests/test_pulse.py -v                         # synthetic hook events, no keyboard needed
```

If no writable hidraw node for vendor `3151` is found on Linux, the script falls back to
`iot_driver set-led`, without dazzle support; on Windows it logs and does nothing. Runtime state
lives in `$XDG_RUNTIME_DIR/monsgeek-claude-pulse/` (`%LOCALAPPDATA%\monsgeek-claude-pulse\` on
Windows): one file per session, `last-applied`, `previous-led.json` while a snapshot is held, and
`stage-<project>.json` per hinted project. Sessions whose Claude process has exited are dropped
automatically, so a killed terminal does not leave a stale "approval" colour behind for long. If
the LEDs ever get stuck, run `pulsectl.py reset` or delete that directory.

## Events used

| Hook event | Matcher | State |
|---|---|---|
| `SessionStart` | | open, or review if the project's hint says so |
| `UserPromptSubmit` | | busy |
| `PostToolUse` | | busy (returns from approval / question) |
| `PreToolUse` | `AskUserQuestion` | review |
| `PermissionRequest` | | approval (fires as the dialog opens) |
| `Notification` | `permission_prompt` | approval (fallback) |
| `Stop` | | review if the project's hint says so, else `on_stop` (default open) |
| `SessionEnd` | | removes the session |

## Layout

```
.claude-plugin/plugin.json      plugin manifest
.claude-plugin/marketplace.json lets you `claude plugin marketplace add <this dir>`
hooks/hooks.json                event -> scripts/pulse.py
scripts/pulse.py                the hook handler
scripts/pulse_windows.py        Windows HID and process access (ctypes), used by pulse.py there
scripts/pulsectl.py             manual control: set / reset / revert / status / scan / stage
skills/pulse-stage/SKILL.md     tells the agent how and when to leave a stage hint
tests/test_pulse.py             dry-run tests
docs/setup.md                   Linux driver install walkthrough
docs/windows.md                 Windows notes and troubleshooting
vendor/                         (gitignored) clone of monsgeek-akko-linux
```

## License

MIT for this repository. The driver is GPL-3.0 and is not vendored here.
