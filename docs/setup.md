# Setup (Linux)

Windows users: see [windows.md](windows.md) instead; nothing below applies there.

The plugin itself is plain Python and needs no installation. What takes a few steps is getting
unprivileged access to the keyboard's vendor HID interface, which comes from the udev rule shipped
with the [monsgeek-akko-linux](https://github.com/echtzeit-solutions/monsgeek-akko-linux) driver.
The driver's CLI and TUI are also the handiest way to inspect and experiment with the lighting.

Background: these keyboards are not VIA/QMK boards. Their vendor HID interface is usage page
`0xFFFF`, not VIA's `0xFF60`, and OpenRGB does not support them, so there is no off-the-shelf
alternative to the driver.

## 1. Rust toolchain

The driver is written in Rust. If you do not have `cargo`:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
export PATH="$HOME/.cargo/bin:$PATH"
cargo --version
```

## 2. Build dependencies

Debian / Ubuntu family:

```bash
sudo apt install build-essential pkg-config libudev-dev libhidapi-dev protobuf-compiler \
     libssl-dev libpulse-dev libpipewire-0.3-dev libclang-dev
```

For other distributions, map these to your package manager; the upstream README lists Arch,
Fedora and openSUSE equivalents. Why each one is needed, beyond the upstream README's list:

- `protobuf-compiler`: the build script compiles a gRPC proto.
- `libudev-dev`: used by the `hidapi` and `tokio-udev` crates.
- `libssl-dev`: `reqwest` (firmware update client) links OpenSSL.
- `libpulse-dev`: audio-reactive modes link `libpulse` unconditionally; without the dev package the
  final link fails with `cannot find -lpulse`.
- `libpipewire-0.3-dev`, `libclang-dev`: the `screen-capture` feature.

Build with the default features, from the repository root:

```bash
git clone https://github.com/echtzeit-solutions/monsgeek-akko-linux vendor/monsgeek-akko-linux
make -C vendor/monsgeek-akko-linux driver
```

The build uses every core and takes several minutes. Prefix it with `nice -n 19` if the machine
needs to stay responsive meanwhile.

Do not use `--no-default-features`. Upstream's TUI code references `check_firmware` and
`poll_anim_status` without feature gates, so the `firmware-api` and `notify` features are
effectively mandatory and the build fails with E0432 and E0599. If you want to avoid the PipeWire
and clang packages, the only feature that is cleanly optional is `screen-capture`:

```bash
cd vendor/monsgeek-akko-linux/iot_driver_linux
cargo build --release --no-default-features --features firmware-api,notify
```

That variant is not what upstream tests, so prefer `make driver`.

## 3. Install binary and udev rules

```bash
sudo make -C vendor/monsgeek-akko-linux install-driver install-udev
```

This copies `iot_driver` to `/usr/local/bin` and `udev/99-monsgeek.rules` to
`/usr/lib/udev/rules.d`, which makes hidraw nodes for vendor `3151` world read/write. Unplug and
replug the keyboard so the rule applies. Skip plain `make install`: it also installs a desktop
entry and downloads a device database, neither of which this plugin needs.

## 4. Verify the keyboard responds

```bash
iot_driver info
iot_driver --monitor led                     # full decoded LED state, including the dazzle flag
iot_driver set-led breathing 4 3 255 110 0   # pulsing orange
iot_driver set-led constant 4 0 255 200 0    # solid yellow
iot_driver set-led wave 4 2
```

If `iot_driver` cannot find the device, check that `lsusb` lists a `3151:xxxx` device and that
`ls -l /dev/hidraw*` shows `crw-rw-rw-` nodes after replugging. The plugin's own readback is a
second check:

```bash
python3 scripts/pulsectl.py status
```

## 5. Load the plugin

For a trial run, from the repository root:

```bash
claude --plugin-dir .
```

To make it permanent, register the directory as a local marketplace and install from it:

```bash
claude plugin marketplace add "$PWD"
claude plugin install monsgeek-claude-pulse@monsgeek-claude-pulse
```

Then send a prompt and watch the keyboard change, trigger a permission prompt for the approval
colour, and let Claude finish for the subtle open colour.

## 6. Pick your colours

Set the lighting you like using the keyboard's Fn keys or `iot_driver tui`, then capture it:

```bash
python3 scripts/pulsectl.py scan --save busy
```

Repeat for `approval`, `review`, `open` and `idle`. See the README for the config format and the
`restore_previous` option, which puts back whatever the keyboard showed before the first session.

## Notes

- The LED setting is stored on the keyboard, so whatever state was last applied persists after
  Claude Code exits. `SessionEnd` restores the previous lighting (or the `idle` state) when the
  last session closes, and `pulsectl.py reset` does the same by hand.
- `iot_driver led` hides the dazzle flag. `iot_driver --monitor led` shows the full decoded
  response, including `dazzle` and `option_raw`. The stock lighting on the M1 V5 HE is
  `constant`, brightness 2, dazzle on, which looks like a fixed rainbow gradient.
- The plugin writes LED commands over `/dev/hidrawN` itself. The command layout is in the driver's
  `docs/PROTOCOL.md`, section 5.1.
- Hooks inherit your shell environment, so `PULSE_DRY_RUN=1 claude ...` works for a dry run.
- The driver's `notify-daemon` and keyframe animations need patched firmware. This plugin does not
  use them and does not flash anything.
