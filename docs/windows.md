# Windows

The plugin runs on Windows with the same scripts. Claude Code on Windows runs hook commands
through Git Bash, which it needs anyway, so `hooks/hooks.json` is unchanged; it only picks
`python` instead of `python3` there. The keyboard is reached through `hid.dll`
(`scripts/pulse_windows.py`, standard library `ctypes`), so no driver, no udev rule and no
administrator rights are involved: Windows lets any user open a vendor-defined HID collection.

**WSL is not supported.** Claude Code must run on the Windows host. WSL cannot see USB HID
devices, and the hooks would run inside the VM. If you develop in WSL but run Claude Code as the
IDE extension on the host, you are on the host and this applies to you.

## Install

1. Python 3.8 or newer from [python.org](https://www.python.org/downloads/windows/), with
   "Add python.exe to PATH" ticked. The Microsoft Store build works too. Check in Git Bash:
   `python --version`.
2. Git for Windows (Claude Code already requires it).
3. From the repository root, in Git Bash:

   ```bash
   python scripts/pulse_windows.py     # probe: lists the keyboard's HID collections, reads the LEDs
   python scripts/pulsectl.py status   # device path and keyboard readback
   claude --plugin-dir .               # try it
   claude plugin marketplace add "$PWD"
   claude plugin install monsgeek-claude-pulse@monsgeek-claude-pulse
   ```

## Paths

| | Windows | Linux |
|---|---|---|
| config | `%APPDATA%\monsgeek-claude-pulse\config.json` | `~/.config/monsgeek-claude-pulse/config.json` |
| runtime state and log | `%LOCALAPPDATA%\monsgeek-claude-pulse\` | `$XDG_RUNTIME_DIR/monsgeek-claude-pulse/` |

`PULSE_CONFIG`, `PULSE_STATE_DIR`, `PULSE_DRY_RUN` and `PULSE_LOG` work the same.

## Status and verification checklist

The Windows backend was written on Linux on 31 Oct 2026 without a Windows machine. The Linux
tests exercise its code paths through a stand-in, and the module compiles, but the calls into
`setupapi.dll` and `hid.dll` have not run against a real keyboard yet. Work through this list on
the first Windows machine; each step tells you where to look if it fails. Remove this section once
everything passes.

1. **Probe.** `python scripts/pulse_windows.py` should print one line per HID collection of
   vendor `3151`, mark the one with `usage_page=ffff` with `->`, and end with
   `keyboard answers: ['constant', 2, 0, 0, 255, 0, 'dazzle']` or whatever the lighting is.
   - No collections listed: the keyboard is not wired, or the path prefilter missed it. The probe
     then prints every HID path it saw; look for `vid_3151` in them.
   - Collections listed but none marked: compare `usage_page` values; the vendor collection may
     report a different page on this firmware. Adjust `VENDOR_USAGE_PAGE` in `pulse_windows.py`.
   - `feature_len` is not 65: `set_feature` and `get_feature` pad or trim to it automatically,
     but note the value.
   - `HidD_SetFeature` or `HidD_GetFeature` fails with an access error: another program
     (the MonsGeek desktop driver, a browser tab on the MonsGeek web app) may have opened the
     collection exclusively. Close it and retry.
   - A `ctypes` `ArgumentError` or crash: a structure or prototype is wrong. The suspects are
     `SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize` (8 on 64-bit Python) and `HIDP_CAPS` (64 bytes).
2. **Write.** `python scripts/pulsectl.py set approval` should make the keyboard breathe orange,
   `python scripts/pulsectl.py revert` should put the previous lighting back.
3. **Tests.** `python tests/test_pulse.py -v`: all pass. Failures here point at path handling
   (`os.path`, `git rev-parse` in Git Bash versus cmd) rather than HID.
4. **Hooks.** `PULSE_LOG=1 claude --plugin-dir .` in Git Bash, type a prompt, let it finish, exit.
   `%LOCALAPPDATA%\monsgeek-claude-pulse\pulse.log` should show `UserPromptSubmit ... -> busy`,
   `Stop ... -> open`, `SessionEnd`. The `pid=` in a session file should be the `claude.exe` or
   `node.exe` process; `pulsectl.py status` says `alive` or `DEAD`.
   - Hook never runs: check `claude --debug` for the command Git Bash received, and that `python`
     resolves inside Git Bash (`command -v python`).
   - `pid=None`: the process tree walk did not find `claude.exe` or `node.exe` within eight
     parents. Sessions then rely on the 12 h staleness only. Print `process_table()` to see the
     chain.
5. **Stage hint.** `python scripts/pulsectl.py stage review "test"` in a project, then a prompt:
   the Stop hook shows solid orange. `stage clear` afterwards.

Then bump the version in `.claude-plugin/plugin.json`, drop this checklist, and tag.
