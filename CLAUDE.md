# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Daydream Remote Control — a Python script that connects to a Google Daydream VR controller via Bluetooth LE and translates its inputs into mouse movements and keyboard events on Linux using `python-uinput`. Designed for presentation use (e.g., with pympress on Sway/Wayland).

## Running

Python 3.14 (pinned in `.python-version`), managed with uv. Dev tools live in the `dev` dependency group.

```bash
# Dev environment (Python 3.14 + pytest/ruff)
uv sync

# Run from the checkout — no args needed, defaults to "Daydream controller" + the standard sensor UUID
uv run daydream
uv run daydream --debug --map-vol-plus KEY_F5 --map-app KEY_ENTER
uv run daydream --no-ui            # plain logs instead of the live status bar

# Install/refresh the user-wide command (~/.local/bin/daydream and daydream-remote)
uv tool install --managed-python --python 3.14 --reinstall .
```

The user is in the `input` group with a udev rule for `/dev/uinput`, so no sudo is needed.

## Testing

```bash
# Run all tests
uv run pytest -v

# Run specific test file
uv run pytest tests/test_state.py -v

# Lint and format check
uv run ruff format --check daydream.py tests/
uv run ruff check daydream.py tests/
```

Tests mock `uinput` at the `sys.modules` level in `tests/conftest.py` — no `/dev/uinput` or hardware needed.

## Architecture

Single-file application (`daydream.py`) with these key components:

- **`KeyMap` dataclass**: Configurable key mappings for controller buttons (click, vol_plus, vol_minus, app, home). Defaults match original hardcoded values. Has `uinput_events()` method for Device creation.
- **`TouchConfig` dataclass**: Touchpad behavior settings — sensitivity, dead zone, acceleration.
- **`State` class**: Parses the 19-byte BLE sensor data packet. Extracts buttons, touchpad coordinates, and optionally IMU data (`parse_imu` parameter). Uses `_parse_buttons()`, `_parse_touch()`, `_parse_imu()` private methods.
- **`DaydreamBridge` class**: Main controller — encapsulates all state previously held in globals. Key methods:
  - `sensor_notification_handler()` — BLE callback, delegates to `_emit_touch_movement()` and `_emit_button_events()`
  - `run()` — outer reconnect loop; `_connect_and_run()` — single connection session
  - `_reset_handler_state()` — called on each new connection
  - `init_uinput()` / `destroy_uinput()` — lifecycle management
- **`BridgeStatus` dataclass** + **`Phase` enum**: live snapshot (phase, device, battery, held buttons, touch, last action, packet count) written by the bridge on the event loop and read by the UI's refresh thread. Fields are replaced, never mutated in place (e.g. `held` is a `frozenset`), to keep cross-thread reads safe.
- **`StatusLine`**: renders `BridgeStatus` as a two-line rich status bar; `cli_main()` wraps `asyncio.run()` in a `rich.live.Live` with logs routed through `RichHandler` so they scroll above it. Disabled with `--no-ui` or when stdout isn't a TTY.
- **`parse_args()`** / **`configure_logging()`** / **`cli_main()`**: Extracted CLI entry points. `cli_main()` (returns the exit code) is the `[project.scripts]` entry point for both `daydream` and `daydream-remote`. `--name` and the sensor UUID are optional and fall back to `DEFAULT_DEVICE_NAME` / `DEFAULT_SENSOR_UUID`.
- **Battery monitoring**: `setup_battery_monitoring(client, on_level)` with notify-based and periodic-read fallback modes; levels are reported through the `on_level` callback.

## Key Details

- Dependencies: `bleak` (BLE), `python-uinput` (virtual input device), `rich` (status bar)
- Dev dependencies: `pytest`, `pytest-asyncio`, `ruff`
- The controller's sensor characteristic UUID is typically `00000001-1000-1000-8000-00805f9b34fb`
- Button states are bitmask flags on byte 18 of the data packet
- Touch coordinates are normalized to 0.0–1.0 range; mouse sensitivity defaults are X=1000, Y=900
- `uinput.syn()` must be called after emitting relative mouse events for them to take effect
- Click uses press/release tracking (for drag support); other buttons use `emit_click` (one-shot on rising edge)
- Auto-reconnect on disconnect with 5-second retry delay; disconnects are detected via bleak's `disconnected_callback` (plus an `is_connected` poll)
- Shutdown: Ctrl+C reaches `run()` as `asyncio.CancelledError` (not `KeyboardInterrupt`) on Python 3.11+; it must be re-raised. `_connect_and_run()` explicitly calls `_disconnect()` (with a timeout) in a `finally` so the controller's BLE link is always dropped
- While the status bar is shown, `suppress_tty_input()` turns off stdin echo/line buffering (signals still work) so Enter can't break the layout; terminal settings are restored and typed input flushed on exit
- IMU parsing is opt-in via `--parse-imu` flag (skipped by default for performance)
- License: GPL v3
