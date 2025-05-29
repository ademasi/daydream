# -*- coding: utf-8 -*-
"""
Daydream Controller to Mouse/Keyboard for Sway (uinput version + Battery/Info)
-----------------------------------------------------------------------------

Connects to a Daydream controller via Bluetooth LE,
interprets its sensor data, and translates it into
mouse movements, clicks, and keyboard presses using python-uinput.

Also reads and reports Battery Level and Device Information.

- Touch area: Moves mouse cursor.
- Touchpad click: Simulates left mouse click.
- Volume +/- buttons: Emulate Left/Right arrow keys for presentations.
- App button: Emulates Escape key.
- Home button: Emulates Super (Windows/Meta) key.

Requires:
- bleak: For Bluetooth LE communication.
- python-uinput: For creating a virtual input device.

Installation:
  pip install bleak python-uinput
  (Ensure uinput kernel module is loaded and /dev/uinput is accessible)

Usage Example:
  sudo python daydreamv2.py --name "Daydream controller" --characteristic 00000001-1000-1000-8000-00805f9b34fb
  (Or run without sudo after setting up udev rules and ensuring uinput module is loaded)
"""

import argparse
import asyncio
import logging
import math

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError

try:
    import uinput
except ImportError:
    logging.critical(
        "python-uinput library not found. Please install it: pip install python-uinput"
    )
    logging.critical(
        "You may also need system dependencies like libudev-dev and kernel headers."
    )
    exit(1)


logger = logging.getLogger(__name__)

# --- uinput Device Configuration ---
UINPUT_EVENTS = [
    uinput.BTN_LEFT,
    uinput.KEY_LEFT,
    uinput.KEY_RIGHT,
    uinput.KEY_ESC,
    uinput.KEY_LEFTMETA,
    uinput.REL_X,
    uinput.REL_Y,
]
uinput_device = None

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


class State:
    def __init__(self, data):
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
            self.valid = False
            return
        self.valid = True

        self.isClickDown = (data[18] & 0x1) > 0
        self.isAppDown = (data[18] & 0x4) > 0
        self.isHomeDown = (data[18] & 0x2) > 0
        self.isVolPlusDown = (data[18] & 0x10) > 0
        self.isVolMinusDown = (data[18] & 0x8) > 0

        self.time = (data[0] & 0xFF) << 1 | (data[1] & 0x80) >> 7
        self.seq = (data[1] & 0x7C) >> 2
        xOri = (
            (data[1] & 0x03) << 11
            | (data[2] & 0xFF) << 3
            | (data[3] & 0x80) >> 5
        )
        xOri = (xOri << 19) >> 19
        self.xOri = xOri * (2 * math.pi / 4095.0)
        yOri = (data[3] & 0x1F) << 8 | (data[4] & 0xFF)
        yOri = (yOri << 19) >> 19
        self.yOri = yOri * (2 * math.pi / 4095.0)
        zOri = (data[5] & 0xFF) << 5 | (data[6] & 0xF8) >> 3
        zOri = (zOri << 19) >> 19
        self.zOri = zOri * (2 * math.pi / 4095.0)
        xAcc = (
            (data[6] & 0x07) << 10
            | (data[7] & 0xFF) << 2
            | (data[8] & 0xC0) >> 6
        )
        xAcc = (xAcc << 19) >> 19
        self.xAcc = xAcc * (8 * 9.8 / 4095.0)
        yAcc = (data[8] & 0x3F) << 7 | (data[9] & 0xFE) >> 1
        yAcc = (yAcc << 19) >> 19
        self.yAcc = yAcc * (8 * 9.8 / 4095.0)
        zAcc = (
            (data[9] & 0x01) << 12
            | (data[10] & 0xFF) << 4
            | (data[11] & 0xF0) >> 4
        )
        zAcc = (zAcc << 19) >> 19
        self.zAcc = zAcc * (8 * 9.8 / 4095.0)
        xGyro = (
            (data[11] & 0x0F) << 9
            | (data[12] & 0xFF) << 1
            | (data[13] & 0x80) >> 7
        )
        xGyro = (xGyro << 19) >> 19
        self.xGyro = xGyro * (2048 / 180 * math.pi / 4095.0)
        yGyro = (data[13] & 0x7F) << 6 | (data[14] & 0xFC) >> 2
        yGyro = (yGyro << 19) >> 19
        self.yGyro = yGyro * (2048 / 180 * math.pi / 4095.0)
        zGyro = (
            (data[14] & 0x03) << 11
            | (data[15] & 0xFF) << 3
            | (data[16] & 0xE0) >> 5
        )
        zGyro = (zGyro << 19) >> 19
        self.zGyro = zGyro * (2048 / 180 * math.pi / 4095.0)
        x_touch_raw = (data[16] & 0x1F) << 3 | (data[17] & 0xE0) >> 5
        y_touch_raw = (data[17] & 0x1F) << 3 | (data[18] & 0xE0) >> 5
        self.x_touch = x_touch_raw / 255.0
        self.y_touch = y_touch_raw / 255.0


# --- Global state tracking for event handling ---
_handler_state = {
    "prev_state": None,
    "last_touch_x": 0.0,
    "last_touch_y": 0.0,
    "finger_on_pad": False,
}
MOUSE_SENSITIVITY_X = 1000
MOUSE_SENSITIVITY_Y = 900
battery_task = None  # To hold the periodic read task if needed


def sensor_notification_handler(
    characteristic: BleakGATTCharacteristic, data: bytearray
):
    """Handles SENSOR notifications from the Daydream controller."""
    global \
        _handler_state, \
        uinput_device, \
        MOUSE_SENSITIVITY_X, \
        MOUSE_SENSITIVITY_Y

    if not uinput_device:
        logger.warning("uinput device not initialized, skipping notification.")
        return

    current_state = State(data)
    if not current_state.valid:
        return

    prev_state = _handler_state["prev_state"]
    last_touch_x = _handler_state["last_touch_x"]
    last_touch_y = _handler_state["last_touch_y"]
    finger_on_pad = _handler_state["finger_on_pad"]

    if prev_state is None:
        _handler_state["prev_state"] = current_state
        if current_state.x_touch != 0.0 or current_state.y_touch != 0.0:
            _handler_state["last_touch_x"] = current_state.x_touch
            _handler_state["last_touch_y"] = current_state.y_touch
            _handler_state["finger_on_pad"] = True
        return

    current_finger_on_pad = (
        current_state.x_touch != 0.0 or current_state.y_touch != 0.0
    )
    moved = False
    if current_finger_on_pad:
        if not finger_on_pad:
            _handler_state["last_touch_x"] = current_state.x_touch
            _handler_state["last_touch_y"] = current_state.y_touch
        else:
            dx = (current_state.x_touch - last_touch_x) * MOUSE_SENSITIVITY_X
            dy = (current_state.y_touch - last_touch_y) * MOUSE_SENSITIVITY_Y
            if abs(dx) > 0.01:
                uinput_device.emit(uinput.REL_X, int(round(dx)))
                moved = True
            if abs(dy) > 0.01:
                uinput_device.emit(uinput.REL_Y, int(round(dy)))
                moved = True
            _handler_state["last_touch_x"] = current_state.x_touch
            _handler_state["last_touch_y"] = current_state.y_touch
    if moved:
        uinput_device.syn()  # CORRECTED
    _handler_state["finger_on_pad"] = current_finger_on_pad

    if current_state.isClickDown and not prev_state.isClickDown:
        logger.debug("Touchpad Press (BTN_LEFT down)")
        uinput_device.emit(uinput.BTN_LEFT, 1)
        uinput_device.syn()  # CORRECTED
    elif not current_state.isClickDown and prev_state.isClickDown:
        logger.debug("Touchpad Release (BTN_LEFT up)")
        uinput_device.emit(uinput.BTN_LEFT, 0)
        uinput_device.syn()  # CORRECTED

    if current_state.isVolPlusDown and not prev_state.isVolPlusDown:
        logger.info("Volume Plus Pressed (-> Right Arrow)")
        uinput_device.emit_click(uinput.KEY_RIGHT)

    if current_state.isVolMinusDown and not prev_state.isVolMinusDown:
        logger.info("Volume Minus Pressed (<- Left Arrow)")
        uinput_device.emit_click(uinput.KEY_LEFT)

    if current_state.isAppDown and not prev_state.isAppDown:
        logger.info("App Button Pressed (Escape)")
        uinput_device.emit_click(uinput.KEY_ESC)

    if current_state.isHomeDown and not prev_state.isHomeDown:
        logger.info("Home Button Pressed (Super/Meta Key)")
        uinput_device.emit_click(uinput.KEY_LEFTMETA)

    _handler_state["prev_state"] = current_state


def battery_notification_handler(
    characteristic: BleakGATTCharacteristic, data: bytearray
):
    """Handles BATTERY notifications."""
    battery_level = int(data[0])
    logger.info(f"** Battery Level Updated: {battery_level}% **")


async def periodic_battery_read(
    client: BleakClient, char_uuid: str, interval: int = 300
):
    """Reads battery level periodically if notifications aren't supported."""
    logger.info(f"Starting periodic battery check (every {interval} seconds).")
    while client.is_connected:
        try:
            value = await client.read_gatt_char(char_uuid)
            battery_level = int(value[0])
            logger.info(f"** Battery Level Check: {battery_level}% **")
        except BleakError as e:
            logger.warning(f"Failed to read battery level periodically: {e}")
            break  # Stop trying if it fails (e.g., disconnection)
        except Exception as e_inner:
            logger.error(
                f"Unexpected error during periodic battery read: {e_inner}",
                exc_info=True,
            )
            break
        await asyncio.sleep(interval)
    logger.info("Periodic battery check stopped.")


async def read_device_info(client: BleakClient):
    """Reads and logs standard Device Information characteristics."""
    logger.info("--- Reading Device Information ---")
    found_service = False
    try:
        for service in client.services:
            if service.uuid.lower() == DEVICE_INFO_SERVICE_UUID.lower():
                found_service = True
                for char in service.characteristics:
                    if char.uuid.lower() in DEVICE_INFO_CHARS_MAP:
                        if "read" in char.properties:
                            try:
                                value = await client.read_gatt_char(char.uuid)
                                logger.info(
                                    f"  {DEVICE_INFO_CHARS_MAP[char.uuid.lower()]}: {value.decode('utf-8', errors='replace')}"
                                )
                            except BleakError as e:
                                logger.warning(
                                    f"  Could not read {DEVICE_INFO_CHARS_MAP[char.uuid.lower()]}: {e}"
                                )
                            except Exception as e_inner:
                                logger.warning(
                                    f"  Error decoding {DEVICE_INFO_CHARS_MAP[char.uuid.lower()]}: {e_inner}"
                                )
                        else:
                            logger.debug(
                                f"  {DEVICE_INFO_CHARS_MAP[char.uuid.lower()]} found but not readable."
                            )
                break  # Found the service, no need to check others
    except Exception as e:
        logger.error(
            f"Error discovering/reading device info: {e}", exc_info=True
        )

    if not found_service:
        logger.warning("Device Information Service (180a) not found.")
    logger.info("--------------------------------")


async def setup_battery_monitoring(client: BleakClient) -> asyncio.Task | None:
    """Sets up battery monitoring (notify or periodic read) and returns a task if needed."""
    logger.info("--- Setting up Battery Monitoring ---")
    try:
        for service in client.services:
            if service.uuid.lower() == BATTERY_SERVICE_UUID.lower():
                for char in service.characteristics:
                    if char.uuid.lower() == BATTERY_LEVEL_UUID.lower():
                        if "notify" in char.properties:
                            logger.info(
                                "  Battery Level supports NOTIFY. Subscribing..."
                            )
                            await client.start_notify(
                                char.uuid, battery_notification_handler
                            )
                            # Read once immediately for initial value
                            value = await client.read_gatt_char(char.uuid)
                            logger.info(
                                f"  Initial Battery Level: {int(value[0])}%"
                            )
                            logger.info("-----------------------------------")
                            return None  # Notify set up, no task needed
                        elif "read" in char.properties:
                            logger.info(
                                "  Battery Level supports READ, not NOTIFY. Setting up periodic check..."
                            )
                            # Read once immediately
                            value = await client.read_gatt_char(char.uuid)
                            logger.info(
                                f"  Initial Battery Level: {int(value[0])}%"
                            )
                            logger.info("-----------------------------------")
                            return asyncio.create_task(
                                periodic_battery_read(client, char.uuid)
                            )
                        else:
                            logger.warning(
                                "  Battery Level characteristic found but neither notify nor read supported."
                            )
                            logger.info("-----------------------------------")
                            return None
        logger.warning("Battery Service (180f) or Level (2a19) not found.")
    except Exception as e:
        logger.error(f"Error setting up battery monitoring: {e}", exc_info=True)
    logger.info("-----------------------------------")
    return None


async def main(cli_args: argparse.Namespace):
    global MOUSE_SENSITIVITY_X, MOUSE_SENSITIVITY_Y, uinput_device, battery_task
    MOUSE_SENSITIVITY_X = cli_args.sensitivity_x
    MOUSE_SENSITIVITY_Y = cli_args.sensitivity_y

    logger.info("Starting Daydream controller script (uinput + info)...")
    logger.info(
        f"Targeting: Name='{cli_args.name}', Address='{cli_args.address}', Characteristic='{cli_args.characteristic}'"
    )
    logger.info(
        f"Mouse sensitivity: X={MOUSE_SENSITIVITY_X}, Y={MOUSE_SENSITIVITY_Y}"
    )

    try:
        uinput_device = uinput.Device(
            UINPUT_EVENTS, name="Daydream Virtual Controller"
        )
        logger.info("Successfully created uinput virtual device.")
    except OSError as e:
        logger.error(
            f"Failed to create uinput device: {e}. Ensure uinput kernel module is loaded and /dev/uinput is accessible (try 'sudo' or set up udev rules)."
        )
        return
    except Exception as e_uinput_init:
        logger.error(
            f"An unexpected error occurred during uinput initialization: {e_uinput_init}",
            exc_info=True,
        )
        return

    device = None
    if cli_args.address:
        device = await BleakScanner.find_device_by_address(
            cli_args.address,
            cb=dict(use_bdaddr=cli_args.macos_use_bdaddr),
            timeout=10.0,
        )
    else:
        logger.info(
            f"Scanning for device by name '{cli_args.name}'. This may take up to 60 seconds..."
        )
        for attempt in range(3):
            try:
                device = await BleakScanner.find_device_by_name(
                    cli_args.name,
                    cb=dict(use_bdaddr=cli_args.macos_use_bdaddr),
                    timeout=20.0,
                )
                if device:
                    break
            except Exception as e:
                logger.warning(f"Error during scan attempt {attempt + 1}: {e}")
            if not device:
                logger.warning(
                    f"Device '{cli_args.name}' not found on attempt {attempt + 1}. Retrying after 2s..."
                )
                await asyncio.sleep(2.0)

    if device is None:
        logger.error("Could not find device. Ensure it's on and discoverable.")
        if uinput_device:
            uinput_device.destroy()
        return

    logger.info(f"Found device: {device.name} ({device.address})")

    try:
        async with BleakClient(device) as client:
            if not client.is_connected:
                logger.error(f"Failed to connect to {device.address}.")
                return
            logger.info(f"Successfully connected to {device.name}")

            # --- Read Info and Setup Battery ---
            await read_device_info(client)
            battery_task = await setup_battery_monitoring(client)

            # --- Setup Main Sensor Notifications ---
            logger.info(
                f"Starting sensor notifications on characteristic {cli_args.characteristic}..."
            )
            await client.start_notify(
                cli_args.characteristic, sensor_notification_handler
            )

            logger.info(
                "Daydream controller is now active. Press Ctrl+C to stop."
            )
            while True:
                if not client.is_connected:
                    logger.warning("Client disconnected unexpectedly. Exiting.")
                    break
                await asyncio.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down...")
    except BleakError as e_bleak:
        logger.error(f"A Bluetooth error occurred: {e_bleak}", exc_info=True)
    except Exception as e_main:
        logger.error(f"An unexpected error occurred: {e_main}", exc_info=True)
    finally:
        logger.info("Cleaning up...")
        if battery_task and not battery_task.done():
            logger.info("Cancelling periodic battery task...")
            battery_task.cancel()
            try:
                await battery_task  # Allow cancellation to complete
            except asyncio.CancelledError:
                pass  # Expected
        if uinput_device:
            logger.info("Destroying uinput virtual device...")
            try:
                uinput_device.destroy()
            except Exception as e_uinput_destroy:
                logger.warning(
                    f"Error destroying uinput device: {e_uinput_destroy}"
                )
        logger.info("Script finished.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Control mouse/keyboard using a Daydream controller (uinput + info)."
    )
    device_group = parser.add_mutually_exclusive_group(required=True)
    device_group.add_argument(
        "--name",
        metavar="<name>",
        help="Name of the Bluetooth device (e.g., 'Daydream controller').",
    )
    device_group.add_argument(
        "--address",
        metavar="<address>",
        help="MAC/UUID address of the Bluetooth device.",
    )
    parser.add_argument(
        "characteristic",
        metavar="<notify_uuid>",
        help="UUID of the SENSOR characteristic for notifications (e.g., '00000001-1000-1000-8000-00805f9b34fb').",
    )
    parser.add_argument(
        "--macos-use-bdaddr",
        action="store_true",
        help="Use Bluetooth address instead of UUID on macOS for device identification.",
    )
    parser.add_argument(
        "--sensitivity-x",
        type=int,
        default=MOUSE_SENSITIVITY_X,
        help=f"Mouse X sensitivity. Default: {MOUSE_SENSITIVITY_X}",
    )
    parser.add_argument(
        "--sensitivity-y",
        type=int,
        default=MOUSE_SENSITIVITY_Y,
        help=f"Mouse Y sensitivity. Default: {MOUSE_SENSITIVITY_Y}",
    )
    parser.add_argument(
        "-d", "--debug", action="store_true", help="Enable debug logging."
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)-15s %(name)-8s %(levelname)s: %(message)s",
    )
    if not args.debug:
        logging.getLogger("bleak.backends.bluezdbus.manager").setLevel(
            logging.WARNING
        )
        logging.getLogger("bleak.backends.bluezdbus.client").setLevel(
            logging.WARNING
        )

    asyncio.run(main(args))
