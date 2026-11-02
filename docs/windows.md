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

## Restore the lighting at logon

If Windows restarts while a Claude session is live, the keyboard keeps the plugin's colour until
the next session ends. A shortcut in your Startup folder runs `pulsectl.py reset` at every logon
and puts the snapshot, or the saved `idle` state, back. No administrator rights are needed, unlike
a Task Scheduler logon task.

First store the lighting you want back as `idle` (the snapshot in `%LOCALAPPDATA%` is used when
present, this is the fallback):

```bash
python scripts/pulsectl.py revert           # if a snapshot is held, show that lighting first
python scripts/pulsectl.py scan --save idle # store what the keyboard shows now
```

Then create the shortcut, in PowerShell from the repository root. `pythonw.exe` runs without a
console window:

```powershell
$py  = (Get-Command pythonw.exe).Source
$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "monsgeek-claude-pulse reset.lnk"
$s   = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath       = $py
$s.Arguments        = "`"$PWD\scripts\pulsectl.py`" reset"
$s.WorkingDirectory = "$PWD"
$s.Description      = "Put the keyboard lighting back after a restart"
$s.Save()
```

Check it with `explorer shell:startup`, and test it by double-clicking the shortcut. Delete the
shortcut to remove it. If you prefer Task Scheduler, the equivalent is an "At log on" task for your
user running the same command, which needs an elevated prompt to create.

## Troubleshooting

Verified on 31 Oct 2026: Windows 11, Python 3.14, the native `claude.exe`, MonsGeek M1 V5 HE
(`3151:5030`). The vendor collection shows up as interface `mi_02`, usage page `ffff`, usage `02`,
with a 65-byte feature report, and the keyboard answers `GET_LEDPARAM` on the first try.

- **`pulse_windows.py` lists no collections.** The keyboard is not wired, or it is not vendor
  `3151`. The probe then prints every HID path it saw; look for `vid_3151` in them.
- **Collections listed but none marked `->`.** The vendor collection reports a different usage
  page on this firmware. Compare the `usage_page` column and adjust `VENDOR_USAGE_PAGE`.
- **`HidD_SetFeature` or `HidD_GetFeature` fails with an access error.** Another program (the
  MonsGeek desktop driver, a browser tab on the MonsGeek web app) has the collection open
  exclusively. Close it and retry.
- **Hook never runs.** `claude --debug` shows the command Git Bash received; check that `python`
  resolves inside Git Bash (`command -v python`). `PULSE_LOG=1 claude --plugin-dir .` then writes
  one line per event to `%LOCALAPPDATA%\monsgeek-claude-pulse\pulse.log`, including the Claude
  Code pid it found.
- **`pid=None` in the log.** The process tree walk did not reach `claude.exe` or `node.exe` within
  eight parents. Sessions then expire by the 12 h staleness rule only. This happens when the hook
  is launched through an extra forking shell layer (a pipeline, a wrapper script), because a
  Git Bash process that forks and execs leaves a dead parent pid in the Windows chain. Claude
  Code's own hook launch does not do this.
- **Stage hint ignored.** Hints are keyed by `git rev-parse --show-toplevel` of the hook's `cwd`.
  That only matches `pulsectl.py stage` if both see a Windows path (`C:\...`); an MSYS path
  (`/c/...`) handed to a native `git.exe` fails and falls back to a different key.
