# monsgeek-claude-pulse

Visual feedback for [Claude Code](https://code.claude.com) on a MonsGeek / Akko keyboard's RGB
lighting. The keyboard shows what the agent is doing, so you can look away from the terminal:

| Claude Code state | Default lighting |
|---|---|
| working on your prompt | rainbow |
| waiting for a permission decision | pulsing orange |
| asked you a question, or finished its turn | solid yellow |
| no live sessions | whatever the keyboard showed before, or a configured idle mode |

All four are configurable, and you can capture any lighting the keyboard is currently showing
with one command.

## How it works

It is a Claude Code plugin made of [hooks](https://code.claude.com/docs/en/hooks). Each hook runs
`scripts/pulse.py`, which records the session's state, picks the most urgent state across all
running sessions, and only talks to the keyboard when that state changes.

The script writes the firmware's `SET_LEDPARAM` command straight to the keyboard's vendor hidraw
node, following the protocol reverse-engineered by
[monsgeek-akko-linux](https://github.com/echtzeit-solutions/monsgeek-akko-linux) for the RongYuan
RY5088 / YC3121 firmware used in MonsGeek and Akko Hall-effect boards. That driver is still
needed for its udev rule (hidraw access without root) and is the easiest way to read and
experiment with the lighting (`iot_driver led`, `iot_driver tui`).

## Requirements

- Linux with a MonsGeek or Akko keyboard on RY5088 / YC3121 firmware (USB vendor ID `3151`).
  Tested with a MonsGeek M1 V5 HE wired (`3151:5030`). Check the driver's device list for others.
- Python 3.8 or newer. No third-party packages.
- Claude Code 2.1 or newer.
- The `monsgeek-akko-linux` driver, for its udev rule. See [docs/setup.md](docs/setup.md).

## Setup

Full walkthrough in [docs/setup.md](docs/setup.md). Short version, from the repository root:

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

Optional file `~/.config/monsgeek-claude-pulse/config.json`. Each state is
`[mode, brightness 0-4, speed 0-4, r, g, b]`, the same order as `iot_driver set-led`, plus an
optional trailing `"dazzle"`. Mode names and numbers are in the driver's
[CLI reference](https://github.com/echtzeit-solutions/monsgeek-akko-linux/blob/master/docs/CLI.md).

```json
{
  "restore_previous": true,
  "states": {
    "approval": ["breathing", 4, 4, 255, 50, 0],
    "waiting":  ["constant", 4, 0, 255, 50, 0],
    "busy":     ["snake", 4, 2, 37, 111, 97],
    "idle":     ["constant", 2, 0, 0, 255, 0, "dazzle"]
  }
}
```

- `restore_previous` (default `true`): snapshot whatever the keyboard is showing when the first
  session takes over, and put it back when the last session ends. `idle` is then only a fallback
  for when no snapshot exists. Set it to `false` to always end on the `idle` spec instead. If the keyboard is already showing one of the plugin's own states at takeover, because
  hooks died earlier and left it there, nothing is snapshotted.
- `"dazzle"` sets the firmware's colour-cycle flag. On `constant` it gives the static rainbow
  gradient these keyboards ship with. `iot_driver set-led` cannot set it, which is one reason the
  script talks to hidraw directly.
- Colours on these LEDs lean towards green, so an orange that looks right on screen can come out
  yellow on the keys. Tune by eye, then capture with `scan` below.

The easiest way to build a config is to set the lighting you want using the keyboard's Fn keys or
`iot_driver tui`, then run `scan --save <state>` for each state.

## Manual control

If the hooks go missing, for example after a force-quit of the agent or the IDE, the keyboard
stays on whatever was last applied. `scripts/pulsectl.py` fixes that by hand:

```bash
python3 scripts/pulsectl.py reset            # forget all sessions, apply idle (or the snapshot)
python3 scripts/pulsectl.py revert           # put back the lighting snapshotted at takeover
python3 scripts/pulsectl.py set approval     # apply one state: approval, waiting, busy, idle
python3 scripts/pulsectl.py status           # tracked sessions, last applied, keyboard readback
python3 scripts/pulsectl.py scan             # print the keyboard's current lighting as a config spec
python3 scripts/pulsectl.py scan --save busy # ...and store it as that state in the config
python3 scripts/pulsectl.py list             # show the configured states
```

A shell alias keeps it short:

```bash
alias pulsectl="python3 $PWD/scripts/pulsectl.py"
```

## Debugging

```bash
PULSE_DRY_RUN=1 PULSE_LOG=1 claude --plugin-dir .      # log what would be sent, touch nothing
cat "${XDG_RUNTIME_DIR:-/tmp}/monsgeek-claude-pulse/pulse.log"
python3 tests/test_pulse.py -v                         # synthetic hook events, no keyboard needed
```

If no writable hidraw node for vendor `3151` is found, the script falls back to
`iot_driver set-led`, without dazzle support. Runtime state lives in
`$XDG_RUNTIME_DIR/monsgeek-claude-pulse/`: one file per session, `last-applied`, and
`previous-led.json` while a snapshot is held. Sessions whose Claude process has exited are
dropped automatically, so a killed terminal does not leave a stale "approval" colour behind for
long. If the LEDs ever get stuck, run `pulsectl.py reset` or delete that directory.

## Events used

| Hook event | Matcher | State |
|---|---|---|
| `SessionStart` | | waiting |
| `UserPromptSubmit` | | busy |
| `PostToolUse` | | busy (returns from approval / question) |
| `PreToolUse` | `AskUserQuestion` | waiting |
| `Notification` | `permission_prompt` | approval |
| `Stop` | | waiting |
| `SessionEnd` | | removes the session |

## Layout

```
.claude-plugin/plugin.json      plugin manifest
.claude-plugin/marketplace.json lets you `claude plugin marketplace add <this dir>`
hooks/hooks.json                event -> scripts/pulse.py
scripts/pulse.py                the hook handler
scripts/pulsectl.py             manual control: set / reset / revert / status / scan
tests/test_pulse.py             dry-run tests
docs/setup.md                   driver install walkthrough
vendor/                         (gitignored) clone of monsgeek-akko-linux
```

## License

MIT for this repository. The driver is GPL-3.0 and is not vendored here.
