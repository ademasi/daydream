"""
Daydream Controller to Mouse/Keyboard for Linux (BLE + uinput)
---------------------------------------------------------------

Connects to a Google Daydream controller via Bluetooth LE, interprets its
sensor data, and translates it into mouse movements, clicks, and key presses
through a virtual uinput device. Also reports battery level and device info.

- Touch area: Moves mouse cursor.
- Touchpad click: Left mouse button (press/release, so dragging works).
- Volume +/- buttons: Right/Left arrow keys (next/previous slide).
- App button: Escape.
- Home button: Super (Windows/Meta).

All button mappings are configurable with the --map-* options.

While running in a terminal, a live status bar shows the connection state,
battery, packet rate, held buttons, touch position, and the last action.

Usage:
  daydream                                  # defaults: "Daydream controller"
  daydream --address 54:AB:3A:F2:ED:07      # connect by MAC address
  daydream --map-vol-plus KEY_F5 --debug    # remap a button, verbose logs
  daydream --help

Requires the uinput kernel module and write access to /dev/uinput
(see the README for the udev rule).
"""

import argparse
import asyncio
import contextlib
import logging
import math
import sys
import termios
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.logging import RichHandler
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

try:
    import uinput
except ImportError:
    sys.exit(
        "python-uinput is not installed. Install the project with 'uv sync' "
        "(it needs libudev headers and a C compiler to build)."
    )


logger = logging.getLogger(__name__)

type UinputEvent = tuple[int, int]

# --- Controller defaults ---
DEFAULT_DEVICE_NAME = "Daydream controller"
DEFAULT_SENSOR_UUID = "00000001-1000-1000-8000-00805f9b34fb"

# --- Standard Bluetooth UUIDs ---
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
DEVICE_INFO_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
DEVICE_INFO_CHARS_MAP = {
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model Number",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Rev",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Rev",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Rev",
}

# --- Key name mapping for CLI ---
KEY_NAME_MAP = {
    "BTN_LEFT": uinput.BTN_LEFT,
    "KEY_LEFT": uinput.KEY_LEFT,
    "KEY_RIGHT": uinput.KEY_RIGHT,
    "KEY_ESC": uinput.KEY_ESC,
    "KEY_LEFTMETA": uinput.KEY_LEFTMETA,
    "KEY_SPACE": uinput.KEY_SPACE,
    "KEY_ENTER": uinput.KEY_ENTER,
    "KEY_UP": uinput.KEY_UP,
    "KEY_DOWN": uinput.KEY_DOWN,
    "KEY_F5": uinput.KEY_F5,
    "KEY_F11": uinput.KEY_F11,
}

# Human-friendly names for the status bar
KEY_LABELS = {
    "BTN_LEFT": "Click",
    "KEY_LEFT": "Left",
    "KEY_RIGHT": "Right",
    "KEY_ESC": "Esc",
    "KEY_LEFTMETA": "Super",
    "KEY_SPACE": "Space",
    "KEY_ENTER": "Enter",
    "KEY_UP": "Up",
    "KEY_DOWN": "Down",
    "KEY_F5": "F5",
    "KEY_F11": "F11",
}
_EVENT_NAMES = {event: name for name, event in KEY_NAME_MAP.items()}

RECONNECT_DELAY = 5
DISCONNECT_TIMEOUT = 5.0
SCAN_ATTEMPTS = 3


def key_label(event: UinputEvent) -> str:
    """Return a short display label for a uinput key event."""
    name = _EVENT_NAMES.get(event)
    if name is None:
        return str(event)
    return KEY_LABELS.get(name, name)


# --- Configuration dataclasses ---


@dataclass
class KeyMap:
    """Configurable key mappings for controller buttons."""

    click: UinputEvent | None = None
    vol_plus: UinputEvent | None = None
    vol_minus: UinputEvent | None = None
    app: UinputEvent | None = None
    home: UinputEvent | None = None

    def __post_init__(self):
        if self.click is None:
            self.click = uinput.BTN_LEFT
        if self.vol_plus is None:
            self.vol_plus = uinput.KEY_RIGHT
        if self.vol_minus is None:
            self.vol_minus = uinput.KEY_LEFT
        if self.app is None:
            self.app = uinput.KEY_ESC
        if self.home is None:
            self.home = uinput.KEY_LEFTMETA

    def uinput_events(self) -> list:
        """Return the list of uinput events needed for Device creation."""
        events = {self.click, self.vol_plus, self.vol_minus, self.app, self.home}
        events.add(uinput.REL_X)
        events.add(uinput.REL_Y)
        return list(events)


@dataclass
class TouchConfig:
    """Configuration for touchpad behavior."""

    sensitivity_x: int = 1000
    sensitivity_y: int = 900
    dead_zone: float = 0.01
    acceleration: bool = False
    acceleration_factor: float = 1.5
    acceleration_threshold: float = 0.03


# --- Live status shared with the terminal UI ---


class Phase(StrEnum):
    STARTING = "starting"
    SCANNING = "scanning"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class BridgeStatus:
    """Snapshot of the bridge state, written by the bridge and read by the UI.

    Fields are only ever replaced (never mutated in place), so the UI's refresh
    thread can read them safely while the event loop updates them.
    """

    phase: Phase = Phase.STARTING
    detail: str = ""
    target: str = ""
    device_name: str | None = None
    device_address: str | None = None
    device_info: dict[str, str] = field(default_factory=dict)
    battery: int | None = None
    held: frozenset[str] = frozenset()
    touch: tuple[float, float] | None = None
    last_action: str | None = None
    last_action_at: float = 0.0
    packets: int = 0
    connections: int = 0
    started_at: float = field(default_factory=time.monotonic)
    connected_at: float | None = None

    def set_phase(self, phase: Phase, detail: str = ""):
        self.phase = phase
        self.detail = detail


# --- State parser ---


class State:
    def __init__(self, data, parse_imu: bool = True):
        if len(data) < 19:
            logger.warning(
                f"Received data packet of length {len(data)}, expected at least 19. Skipping."
            )
            self.isClickDown = False
            self.isAppDown = False
            self.isHomeDown = False
            self.isVolPlusDown = False
            self.isVolMinusDown = False
            self.x_touch = 0.0
            self.y_touch = 0.0
            self._clear_imu()
            self.valid = False
            return
        self.valid = True

        self._parse_buttons(data)
        self._parse_touch(data)
        if parse_imu:
            self._parse_imu(data)
        else:
            self._clear_imu()

    def _clear_imu(self):
        self.xOri = self.yOri = self.zOri = None
        self.xAcc = self.yAcc = self.zAcc = None
        self.xGyro = self.yGyro = self.zGyro = None

    def _parse_buttons(self, data):
        self.isClickDown = (data[18] & 0x1) > 0
        self.isAppDown = (data[18] & 0x4) > 0
        self.isHomeDown = (data[18] & 0x2) > 0
        self.isVolPlusDown = (data[18] & 0x10) > 0
        self.isVolMinusDown = (data[18] & 0x8) > 0

    def _parse_touch(self, data):
        x_touch_raw = (data[16] & 0x1F) << 3 | (data[17] & 0xE0) >> 5
        y_touch_raw = (data[17] & 0x1F) << 3 | (data[18] & 0xE0) >> 5
        self.x_touch = x_touch_raw / 255.0
        self.y_touch = y_touch_raw / 255.0

    def _parse_imu(self, data):
        self.time = (data[0] & 0xFF) << 1 | (data[1] & 0x80) >> 7
        self.seq = (data[1] & 0x7C) >> 2
        xOri = (data[1] & 0x03) << 11 | (data[2] & 0xFF) << 3 | (data[3] & 0x80) >> 5
        xOri = (xOri << 19) >> 19
        self.xOri = xOri * (2 * math.pi / 4095.0)
        yOri = (data[3] & 0x1F) << 8 | (data[4] & 0xFF)
        yOri = (yOri << 19) >> 19
        self.yOri = yOri * (2 * math.pi / 4095.0)
        zOri = (data[5] & 0xFF) << 5 | (data[6] & 0xF8) >> 3
        zOri = (zOri << 19) >> 19
        self.zOri = zOri * (2 * math.pi / 4095.0)
        xAcc = (data[6] & 0x07) << 10 | (data[7] & 0xFF) << 2 | (data[8] & 0xC0) >> 6
        xAcc = (xAcc << 19) >> 19
        self.xAcc = xAcc * (8 * 9.8 / 4095.0)
        yAcc = (data[8] & 0x3F) << 7 | (data[9] & 0xFE) >> 1
        yAcc = (yAcc << 19) >> 19
        self.yAcc = yAcc * (8 * 9.8 / 4095.0)
        zAcc = (data[9] & 0x01) << 12 | (data[10] & 0xFF) << 4 | (data[11] & 0xF0) >> 4
        zAcc = (zAcc << 19) >> 19
        self.zAcc = zAcc * (8 * 9.8 / 4095.0)
        xGyro = (data[11] & 0x0F) << 9 | (data[12] & 0xFF) << 1 | (data[13] & 0x80) >> 7
        xGyro = (xGyro << 19) >> 19
        self.xGyro = xGyro * (2048 / 180 * math.pi / 4095.0)
        yGyro = (data[13] & 0x7F) << 6 | (data[14] & 0xFC) >> 2
        yGyro = (yGyro << 19) >> 19
        self.yGyro = yGyro * (2048 / 180 * math.pi / 4095.0)
        zGyro = (data[14] & 0x03) << 11 | (data[15] & 0xFF) << 3 | (data[16] & 0xE0) >> 5
        zGyro = (zGyro << 19) >> 19
        self.zGyro = zGyro * (2048 / 180 * math.pi / 4095.0)


# --- Battery and device info helpers ---


def report_battery(data: bytes | bytearray, on_level: Callable[[int], None]):
    """Pass a Battery Level payload to on_level, ignoring empty ones.

    The Daydream controller sometimes sends a zero-length value (in notifications
    and reads alike), which carries no level and must not raise.
    """
    if not data:
        logger.debug("Ignoring empty battery level payload.")
        return
    on_level(int(data[0]))


async def periodic_battery_read(
    client: BleakClient,
    char_uuid: str,
    on_level: Callable[[int], None],
    interval: int = 300,
):
    """Reads battery level periodically if notifications aren't supported."""
    logger.debug(f"Starting periodic battery check (every {interval} seconds).")
    while client.is_connected:
        try:
            value = await client.read_gatt_char(char_uuid)
            report_battery(value, on_level)
        except BleakError as e:
            logger.warning(f"Failed to read battery level periodically: {e}")
            break
        except Exception as e_inner:
            logger.error(
                f"Unexpected error during periodic battery read: {e_inner}",
                exc_info=True,
            )
            break
        await asyncio.sleep(interval)
    logger.debug("Periodic battery check stopped.")


async def read_device_info(client: BleakClient) -> dict[str, str]:
    """Reads the standard Device Information characteristics that are readable."""
    info: dict[str, str] = {}
    try:
        for service in client.services:
            if service.uuid.lower() != DEVICE_INFO_SERVICE_UUID.lower():
                continue
            for char in service.characteristics:
                label = DEVICE_INFO_CHARS_MAP.get(char.uuid.lower())
                if label is None:
                    continue
                if "read" not in char.properties:
                    logger.debug(f"{label} found but not readable.")
                    continue
                try:
                    value = await client.read_gatt_char(char.uuid)
                    info[label] = value.decode("utf-8", errors="replace").strip("\x00 ")
                except BleakError as e:
                    logger.warning(f"Could not read {label}: {e}")
            break
        else:
            logger.warning("Device Information Service (180a) not found.")
    except Exception as e:
        logger.error(f"Error discovering/reading device info: {e}", exc_info=True)

    if info:
        logger.info("Device info: " + ", ".join(f"{k}: {v}" for k, v in info.items()))
    return info


async def setup_battery_monitoring(
    client: BleakClient, on_level: Callable[[int], None]
) -> asyncio.Task | None:
    """Reports the battery level via on_level, using notify or a periodic read.

    Returns the periodic-read task when notifications aren't supported.
    """
    try:
        for service in client.services:
            if service.uuid.lower() != BATTERY_SERVICE_UUID.lower():
                continue
            for char in service.characteristics:
                if char.uuid.lower() != BATTERY_LEVEL_UUID.lower():
                    continue
                if "notify" in char.properties:
                    logger.debug("Battery Level supports NOTIFY. Subscribing...")
                    await client.start_notify(
                        char.uuid, lambda _c, data: report_battery(data, on_level)
                    )
                    report_battery(await client.read_gatt_char(char.uuid), on_level)
                    return None
                if "read" in char.properties:
                    logger.debug("Battery Level supports READ only. Polling periodically.")
                    report_battery(await client.read_gatt_char(char.uuid), on_level)
                    return asyncio.create_task(periodic_battery_read(client, char.uuid, on_level))
                logger.warning("Battery Level found but neither notify nor read is supported.")
                return None
        logger.warning("Battery Service (180f) or Level (2a19) not found.")
    except Exception as e:
        logger.error(f"Error setting up battery monitoring: {e}", exc_info=True)
    return None


# --- Main bridge class ---


class DaydreamBridge:
    """Encapsulates controller state and translates BLE events to uinput."""

    def __init__(
        self,
        key_map: KeyMap | None = None,
        touch_config: TouchConfig | None = None,
        parse_imu: bool = True,
        status: BridgeStatus | None = None,
    ):
        self.key_map = key_map or KeyMap()
        self.touch_config = touch_config or TouchConfig()
        self.parse_imu = parse_imu
        self.status = status or BridgeStatus()
        self.uinput_device = None
        self.battery_task: asyncio.Task | None = None
        self._reset_handler_state()

    def _reset_handler_state(self):
        self._handler_state = {
            "prev_state": None,
            "last_touch_x": 0.0,
            "last_touch_y": 0.0,
            "finger_on_pad": False,
        }

    def init_uinput(self):
        self.uinput_device = uinput.Device(
            self.key_map.uinput_events(), name="Daydream Virtual Controller"
        )
        logger.debug("Created uinput virtual device.")

    def destroy_uinput(self):
        if self.uinput_device:
            logger.debug("Destroying uinput virtual device...")
            try:
                self.uinput_device.destroy()
            except Exception as e:
                logger.warning(f"Error destroying uinput device: {e}")
            self.uinput_device = None

    def sensor_notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ):
        """Handles SENSOR notifications from the Daydream controller."""
        if not self.uinput_device:
            logger.warning("uinput device not initialized, skipping notification.")
            return

        current_state = State(data, parse_imu=self.parse_imu)
        if not current_state.valid:
            return
        self._update_live_status(current_state)

        prev_state = self._handler_state["prev_state"]

        if prev_state is None:
            self._handler_state["prev_state"] = current_state
            if current_state.x_touch != 0.0 or current_state.y_touch != 0.0:
                self._handler_state["last_touch_x"] = current_state.x_touch
                self._handler_state["last_touch_y"] = current_state.y_touch
                self._handler_state["finger_on_pad"] = True
            return

        self._emit_touch_movement(current_state)
        self._emit_button_events(current_state, prev_state)
        self._handler_state["prev_state"] = current_state

    def _update_live_status(self, state: State):
        status = self.status
        status.packets += 1
        status.held = frozenset(
            label
            for label, down in (
                ("CLICK", state.isClickDown),
                ("APP", state.isAppDown),
                ("HOME", state.isHomeDown),
                ("VOL−", state.isVolMinusDown),
                ("VOL+", state.isVolPlusDown),
            )
            if down
        )
        on_pad = state.x_touch != 0.0 or state.y_touch != 0.0
        status.touch = (state.x_touch, state.y_touch) if on_pad else None

    def _record_action(self, button: str, event: UinputEvent):
        action = f"{button} → {key_label(event)}"
        logger.debug(action)
        self.status.last_action = action
        self.status.last_action_at = time.monotonic()

    def _emit_touch_movement(self, current_state: State):
        last_touch_x = self._handler_state["last_touch_x"]
        last_touch_y = self._handler_state["last_touch_y"]
        finger_on_pad = self._handler_state["finger_on_pad"]
        tc = self.touch_config

        current_finger_on_pad = current_state.x_touch != 0.0 or current_state.y_touch != 0.0
        moved = False

        if current_finger_on_pad:
            if not finger_on_pad:
                self._handler_state["last_touch_x"] = current_state.x_touch
                self._handler_state["last_touch_y"] = current_state.y_touch
            else:
                dx = (current_state.x_touch - last_touch_x) * tc.sensitivity_x
                dy = (current_state.y_touch - last_touch_y) * tc.sensitivity_y

                if tc.acceleration:
                    magnitude = (dx**2 + dy**2) ** 0.5
                    if magnitude > tc.acceleration_threshold * tc.sensitivity_x:
                        dx *= tc.acceleration_factor
                        dy *= tc.acceleration_factor

                if abs(dx) > tc.dead_zone:
                    self.uinput_device.emit(uinput.REL_X, int(round(dx)))
                    moved = True
                if abs(dy) > tc.dead_zone:
                    self.uinput_device.emit(uinput.REL_Y, int(round(dy)))
                    moved = True
                self._handler_state["last_touch_x"] = current_state.x_touch
                self._handler_state["last_touch_y"] = current_state.y_touch

        if moved:
            self.uinput_device.syn()
        self._handler_state["finger_on_pad"] = current_finger_on_pad

    def _emit_button_events(self, current: State, prev: State):
        km = self.key_map

        # Click uses press/release tracking (for drag support)
        if current.isClickDown and not prev.isClickDown:
            self._record_action("CLICK", km.click)
            self.uinput_device.emit(km.click, 1)
            self.uinput_device.syn()
        elif not current.isClickDown and prev.isClickDown:
            logger.debug("Touchpad release")
            self.uinput_device.emit(km.click, 0)
            self.uinput_device.syn()

        # Other buttons use emit_click (one-shot on rising edge)
        if current.isVolPlusDown and not prev.isVolPlusDown:
            self._record_action("VOL+", km.vol_plus)
            self.uinput_device.emit_click(km.vol_plus)

        if current.isVolMinusDown and not prev.isVolMinusDown:
            self._record_action("VOL−", km.vol_minus)
            self.uinput_device.emit_click(km.vol_minus)

        if current.isAppDown and not prev.isAppDown:
            self._record_action("APP", km.app)
            self.uinput_device.emit_click(km.app)

        if current.isHomeDown and not prev.isHomeDown:
            self._record_action("HOME", km.home)
            self.uinput_device.emit_click(km.home)

    def _on_battery(self, level: int):
        if level != self.status.battery:
            logger.info(f"Battery: {level}%")
        self.status.battery = level

    async def _find_device(self, cli_args: argparse.Namespace):
        """Scan for the BLE device by address or name."""
        if cli_args.address:
            self.status.set_phase(Phase.SCANNING, f"looking for {cli_args.address}")
            return await BleakScanner.find_device_by_address(
                cli_args.address,
                cb=dict(use_bdaddr=cli_args.macos_use_bdaddr),
                timeout=10.0,
            )

        for attempt in range(1, SCAN_ATTEMPTS + 1):
            self.status.set_phase(
                Phase.SCANNING,
                f"looking for '{cli_args.name}' (attempt {attempt}/{SCAN_ATTEMPTS})"
                " — hold the Home button to wake the controller",
            )
            try:
                device = await BleakScanner.find_device_by_name(
                    cli_args.name,
                    cb=dict(use_bdaddr=cli_args.macos_use_bdaddr),
                    timeout=20.0,
                )
                if device:
                    return device
            except Exception as e:
                logger.warning(f"Error during scan attempt {attempt}: {e}")
            logger.debug(f"Device '{cli_args.name}' not found on attempt {attempt}.")
            await asyncio.sleep(2.0)
        return None

    async def _connect_and_run(self, device, cli_args: argparse.Namespace):
        """Single connection session — connect, subscribe, wait for disconnect."""
        disconnected = asyncio.Event()
        self.status.set_phase(Phase.CONNECTING, f"{device.name} ({device.address})")

        async with BleakClient(
            device, disconnected_callback=lambda _client: disconnected.set()
        ) as client:
            if not client.is_connected:
                logger.error(f"Failed to connect to {device.address}.")
                return

            self._reset_handler_state()
            self.status.device_name = device.name
            self.status.device_address = device.address
            self.status.device_info = await read_device_info(client)
            self.battery_task = await setup_battery_monitoring(client, self._on_battery)

            logger.debug(f"Starting sensor notifications on {cli_args.characteristic}")
            await client.start_notify(cli_args.characteristic, self.sensor_notification_handler)

            self.status.connections += 1
            self.status.connected_at = time.monotonic()
            self.status.set_phase(Phase.CONNECTED)
            logger.info(f"Connected to {device.name} ({device.address}) — controller active")

            try:
                while client.is_connected and not disconnected.is_set():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(disconnected.wait(), timeout=1.0)
                logger.warning("Controller disconnected.")
            finally:
                if client.is_connected:
                    await self._disconnect(client, device)

    async def _disconnect(self, client: BleakClient, device):
        """Explicitly drop the BLE link so the controller doesn't stay connected."""
        self.status.set_phase(Phase.STOPPING, f"disconnecting from {device.name}")
        try:
            await asyncio.wait_for(client.disconnect(), timeout=DISCONNECT_TIMEOUT)
            logger.info(f"Disconnected from {device.name} ({device.address})")
        except (TimeoutError, BleakError) as e:
            logger.warning(f"Could not disconnect cleanly from {device.address}: {e!r}")

    def _clear_connection_status(self):
        self.status.held = frozenset()
        self.status.touch = None
        self.status.connected_at = None

    async def _cancel_battery_task(self):
        if self.battery_task and not self.battery_task.done():
            self.battery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.battery_task
        self.battery_task = None

    async def run(self, cli_args: argparse.Namespace):
        """Main run loop with auto-reconnect."""
        target = cli_args.address or cli_args.name
        self.status.target = target
        tc = self.touch_config
        logger.info(
            f"Daydream bridge starting — target {target!r}, "
            f"sensitivity X={tc.sensitivity_x} Y={tc.sensitivity_y}"
        )

        try:
            self.init_uinput()
        except OSError as e:
            self.status.set_phase(Phase.ERROR, "cannot open /dev/uinput")
            logger.error(
                f"Failed to create uinput device: {e}. Ensure the uinput kernel module is "
                "loaded and /dev/uinput is writable (udev rule + 'input' group, see README)."
            )
            return
        except Exception as e:
            self.status.set_phase(Phase.ERROR, "uinput initialization failed")
            logger.error(
                f"An unexpected error occurred during uinput initialization: {e}",
                exc_info=True,
            )
            return

        try:
            while True:
                device = await self._find_device(cli_args)
                if device is None:
                    self.status.set_phase(
                        Phase.RECONNECTING,
                        f"{target!r} not found — retrying in {RECONNECT_DELAY}s",
                    )
                    logger.warning(f"Could not find {target!r}. Retrying...")
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

                logger.debug(f"Found device: {device.name} ({device.address})")

                try:
                    await self._connect_and_run(device, cli_args)
                except BleakError as e:
                    logger.error(f"Bluetooth error: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error: {e}", exc_info=True)
                finally:
                    self._clear_connection_status()

                await self._cancel_battery_task()

                self.status.set_phase(Phase.RECONNECTING, f"reconnecting in {RECONNECT_DELAY}s")
                await asyncio.sleep(RECONNECT_DELAY)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except asyncio.CancelledError:
            logger.info("Shutting down...")
            raise
        finally:
            self.status.set_phase(Phase.STOPPED)
            await self._cancel_battery_task()
            self.destroy_uinput()


# --- Terminal UI ---


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class StatusLine:
    """Status bar pinned to the bottom of the terminal, rendered from a BridgeStatus."""

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    BADGES = {
        Phase.STARTING: ("○", "black on grey62"),
        Phase.SCANNING: (None, "black on yellow"),
        Phase.CONNECTING: (None, "black on yellow"),
        Phase.CONNECTED: ("●", "black on green"),
        Phase.RECONNECTING: (None, "black on dark_orange"),
        Phase.STOPPING: (None, "white on grey30"),
        Phase.STOPPED: ("■", "white on grey30"),
        Phase.ERROR: ("✕", "white on red"),
    }
    BUTTONS = ("CLICK", "APP", "HOME", "VOL−", "VOL+")
    SEPARATOR = Text(" │ ", style="grey42")

    def __init__(self, status: BridgeStatus):
        self.status = status
        self._rate = 0.0
        self._rate_sample = (time.monotonic(), 0)

    def render(self) -> RenderableType:
        now = time.monotonic()
        self._update_rate(now)
        return Group(
            Rule(style="grey35"),
            self._line(self._summary(), self._clock(now)),
            self._line(self._activity(now)),
        )

    def _update_rate(self, now: float):
        sample_time, sample_packets = self._rate_sample
        if now - sample_time >= 1.0:
            self._rate = (self.status.packets - sample_packets) / (now - sample_time)
            self._rate_sample = (now, self.status.packets)

    @staticmethod
    def _line(left: Text, right: Text | None = None) -> Table:
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        if right is not None:
            grid.add_column(justify="right", no_wrap=True)
            grid.add_row(left, right)
        else:
            grid.add_row(left)
        return grid

    def _badge(self) -> Text:
        phase = self.status.phase
        icon, style = self.BADGES[phase]
        if icon is None:
            icon = self.SPINNER[int(time.monotonic() * 10) % len(self.SPINNER)]
        return Text(f" {icon} {phase.upper()} ", style=f"bold {style}")

    def _summary(self) -> Text:
        s = self.status
        text = Text.assemble(self._badge(), " ")
        if s.device_name:
            text.append(s.device_name, style="bold")
            text.append(f" {s.device_address}", style="grey58")
        else:
            text.append(s.target or "Daydream controller", style="grey70")
        if s.battery is not None:
            text.append_text(self.SEPARATOR)
            text.append_text(self._battery(s.battery))
        if s.phase is Phase.CONNECTED:
            text.append_text(self.SEPARATOR)
            text.append(f"{self._rate:3.0f} Hz", style="cyan")
        return text

    @staticmethod
    def _battery(level: int) -> Text:
        style = "green" if level > 50 else "yellow" if level > 20 else "bold red"
        filled = min(5, max(0, round(level / 20)))
        return Text.assemble(("▰" * filled, style), ("▱" * (5 - filled), "grey42"), f" {level}%")

    def _clock(self, now: float) -> Text:
        s = self.status
        if s.connected_at is not None:
            return Text(f"connected {_format_duration(now - s.connected_at)} ", style="grey58")
        return Text(f"up {_format_duration(now - s.started_at)} ", style="grey58")

    def _activity(self, now: float) -> Text:
        s = self.status
        if s.phase is not Phase.CONNECTED:
            return Text(f" {s.detail}" if s.detail else "", style="italic grey70")

        text = Text(" ")
        held = s.held
        for button in self.BUTTONS:
            style = "bold black on cyan" if button in held else "grey50"
            text.append(f" {button} ", style=style)
        text.append_text(self.SEPARATOR)
        if s.touch is not None:
            text.append("touch ", style="grey58")
            text.append(f"{s.touch[0]:.2f}, {s.touch[1]:.2f}", style="bold")
        else:
            text.append("touch –", style="grey50")
        if s.last_action:
            age = now - s.last_action_at
            text.append_text(self.SEPARATOR)
            text.append("last ", style="grey58")
            text.append(s.last_action, style="bold cyan" if age < 1.5 else "")
            ago = f"{int(age)}s" if age < 60 else _format_duration(age)
            text.append(f" {ago} ago", style="grey50")
        return text


# --- CLI ---


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments. Pass argv=None for sys.argv."""
    parser = argparse.ArgumentParser(
        description=(
            "Use a Google Daydream controller as mouse/keyboard on Linux (BLE + uinput). "
            f"With no arguments, connects to {DEFAULT_DEVICE_NAME!r}."
        )
    )
    device_group = parser.add_mutually_exclusive_group()
    device_group.add_argument(
        "--name",
        metavar="<name>",
        help=f"Name of the Bluetooth device. Default: {DEFAULT_DEVICE_NAME!r}",
    )
    device_group.add_argument(
        "--address",
        metavar="<address>",
        help="MAC/UUID address of the Bluetooth device (instead of --name).",
    )
    parser.add_argument(
        "characteristic",
        metavar="<notify_uuid>",
        nargs="?",
        default=DEFAULT_SENSOR_UUID,
        help=f"UUID of the SENSOR characteristic. Default: {DEFAULT_SENSOR_UUID}",
    )
    parser.add_argument(
        "--macos-use-bdaddr",
        action="store_true",
        help="Use Bluetooth address instead of UUID on macOS for device identification.",
    )
    parser.add_argument(
        "--sensitivity-x",
        type=int,
        default=1000,
        help="Mouse X sensitivity. Default: 1000",
    )
    parser.add_argument(
        "--sensitivity-y",
        type=int,
        default=900,
        help="Mouse Y sensitivity. Default: 900",
    )
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging.")
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Plain log output instead of the live status bar (automatic when not a TTY).",
    )

    # --- Key mapping ---
    key_names = ", ".join(sorted(KEY_NAME_MAP.keys()))
    parser.add_argument(
        "--map-click",
        metavar="KEY",
        choices=KEY_NAME_MAP,
        default=None,
        help=f"Key for touchpad click. Choices: {key_names}",
    )
    parser.add_argument(
        "--map-vol-plus",
        metavar="KEY",
        choices=KEY_NAME_MAP,
        default=None,
        help=f"Key for volume plus button. Choices: {key_names}",
    )
    parser.add_argument(
        "--map-vol-minus",
        metavar="KEY",
        choices=KEY_NAME_MAP,
        default=None,
        help=f"Key for volume minus button. Choices: {key_names}",
    )
    parser.add_argument(
        "--map-app",
        metavar="KEY",
        choices=KEY_NAME_MAP,
        default=None,
        help=f"Key for app button. Choices: {key_names}",
    )
    parser.add_argument(
        "--map-home",
        metavar="KEY",
        choices=KEY_NAME_MAP,
        default=None,
        help=f"Key for home button. Choices: {key_names}",
    )

    # --- Dead zone and acceleration ---
    parser.add_argument(
        "--dead-zone",
        type=float,
        default=0.01,
        help="Touch dead zone threshold. Default: 0.01",
    )
    parser.add_argument(
        "--acceleration",
        action="store_true",
        help="Enable touch acceleration.",
    )
    parser.add_argument(
        "--acceleration-factor",
        type=float,
        default=1.5,
        help="Acceleration multiplier. Default: 1.5",
    )
    parser.add_argument(
        "--acceleration-threshold",
        type=float,
        default=0.03,
        help="Movement threshold to trigger acceleration. Default: 0.03",
    )

    # --- IMU opt-in ---
    parser.add_argument(
        "--parse-imu",
        action="store_true",
        help="Parse IMU data (orientation, accelerometer, gyroscope). Off by default.",
    )

    args = parser.parse_args(argv)
    if not args.name and not args.address:
        args.name = DEFAULT_DEVICE_NAME
    return args


def configure_logging(debug: bool = False, console: Console | None = None):
    """Set up logging. With a console, logs render above the live status bar."""
    log_level = logging.DEBUG if debug else logging.INFO
    if console is not None:
        handler = RichHandler(
            console=console,
            show_path=False,
            rich_tracebacks=True,
            log_time_format="[%X]",
        )
        logging.basicConfig(level=log_level, format="%(message)s", handlers=[handler])
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)-15s %(name)-8s %(levelname)s: %(message)s",
        )
    if not debug:
        logging.getLogger("bleak.backends.bluezdbus.manager").setLevel(logging.WARNING)
        logging.getLogger("bleak.backends.bluezdbus.client").setLevel(logging.WARNING)


@contextlib.contextmanager
def suppress_tty_input() -> Iterator[None]:
    """Stop typed keys (e.g. Enter) from echoing over the live status bar.

    Turns off echo and line buffering on stdin while keeping signals, so Ctrl+C
    still works. Anything typed meanwhile is discarded on exit instead of being
    replayed into the shell.
    """
    if not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    quiet = termios.tcgetattr(fd)
    quiet[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON)
    termios.tcsetattr(fd, termios.TCSANOW, quiet)
    try:
        yield
    finally:
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSANOW, saved)


def cli_main(argv: list[str] | None = None) -> int:
    """Entry point for CLI and pyproject.toml console_scripts."""
    args = parse_args(argv)
    console = Console()
    use_ui = not args.no_ui and console.is_terminal
    configure_logging(args.debug, console if use_ui else None)

    # Build KeyMap from CLI args
    key_map_kwargs = {}
    if args.map_click:
        key_map_kwargs["click"] = KEY_NAME_MAP[args.map_click]
    if args.map_vol_plus:
        key_map_kwargs["vol_plus"] = KEY_NAME_MAP[args.map_vol_plus]
    if args.map_vol_minus:
        key_map_kwargs["vol_minus"] = KEY_NAME_MAP[args.map_vol_minus]
    if args.map_app:
        key_map_kwargs["app"] = KEY_NAME_MAP[args.map_app]
    if args.map_home:
        key_map_kwargs["home"] = KEY_NAME_MAP[args.map_home]
    key_map = KeyMap(**key_map_kwargs)

    touch_config = TouchConfig(
        sensitivity_x=args.sensitivity_x,
        sensitivity_y=args.sensitivity_y,
        dead_zone=args.dead_zone,
        acceleration=args.acceleration,
        acceleration_factor=args.acceleration_factor,
        acceleration_threshold=args.acceleration_threshold,
    )

    bridge = DaydreamBridge(
        key_map=key_map,
        touch_config=touch_config,
        parse_imu=args.parse_imu,
    )

    try:
        if use_ui:
            with (
                suppress_tty_input(),
                Live(
                    get_renderable=StatusLine(bridge.status).render,
                    console=console,
                    refresh_per_second=10,
                    transient=True,
                ),
            ):
                asyncio.run(bridge.run(args))
        else:
            asyncio.run(bridge.run(args))
    except KeyboardInterrupt:
        pass

    if bridge.status.phase is Phase.ERROR:
        return 1
    logger.info("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
