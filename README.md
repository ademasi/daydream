# Daydream Remote Control for Linux (using uinput)

This Python script allows you to use a Google Daydream controller as a mouse and keyboard on Linux systems, particularly well-suited for Wayland compositors like Sway due to its use of `python-uinput`.

It translates touchpad movements and button presses from the Daydream controller into system-level input events, enabling mouse control, presentation navigation, and common keyboard shortcuts. It also attempts to read and report the controller's battery level and device information.

This was made to use this controler for keynote presentations (e.g., with pympress).

## Features

*   **Mouse Control:**
    *   Touchpad area moves the mouse cursor.
    *   Physical click on the touchpad simulates a left mouse button click.
*   **Keyboard Emulation (default mappings):**
    *   **Volume Plus (+)**: Emulates `Right Arrow` (next slide).
    *   **Volume Minus (-)**: Emulates `Left Arrow` (previous slide).
    *   **App Button** (circle/minus icon): Emulates `Escape` key.
    *   **Home Button** (bottom-most): Emulates `Super/Meta/Windows` key.
*   **Configurable Key Mappings:** Every button (click, volume +/-, app, home) can be remapped to a different key via `--map-*` arguments.
*   **Device Information:**
    *   Reads and logs manufacturer, model number, etc., if available via the standard Device Information Service.
*   **Battery Reporting:**
    *   Monitors and logs battery level, using notifications if supported, or periodic reads as a fallback.
*   **Sway/Wayland Compatibility:** Creates a virtual input device via `uinput`, ensuring good compatibility.
*   **Configurable Sensitivity & Touch Tuning:** Mouse sensitivity, dead zone, and optional pointer acceleration are adjustable via command-line arguments.
*   **Auto-Reconnect:** Reconnects automatically if the controller disconnects.
*   **Live Status Bar:** In a terminal, a status line pinned to the bottom shows connection state, battery, packet rate, held buttons, touch position, and the last action, with log messages scrolling above it.

## Prerequisites

### Hardware
*   A Google Daydream controller.
*   A Bluetooth adapter on your Linux machine.

### Software
*   [`uv`](https://docs.astral.sh/uv/) (Python package & project manager) — it fetches Python 3.14 and the dependencies for you
*   `uinput` kernel module (see Setup section)
*   Required Python libraries (installed automatically by `uv`): `bleak`, `python-uinput`, `rich`
*   System dependencies for `python-uinput` (these might vary slightly by distribution):
    *   On Debian/Ubuntu-based systems:
        ```bash
        sudo apt-get update
        sudo apt-get install linux-headers-$(uname -r) libudev-dev python3-dev build-essential
        ```

## Setup Instructions

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/ademasi/daydream
    cd daydream
    ```

2.  **Install the `daydream` command:**
    ```bash
    uv tool install --managed-python --python 3.14 .
    ```
    This puts `daydream` (and the longer alias `daydream-remote`) in `~/.local/bin` (make sure it's on your `PATH`), in its own isolated environment. `--managed-python` uses a uv-managed Python, so upgrading your distribution's Python won't break the tool.

    After pulling changes, reinstall with:
    ```bash
    uv tool install --managed-python --python 3.14 --reinstall .
    ```
    To remove it: `uv tool uninstall daydream-remote`.


3.  **Setup `uinput` Kernel Module:**
    The `uinput` kernel module allows userspace programs to create virtual input devices.

    *   **Check if loaded:**
        ```bash
        lsmod | grep uinput
        ```
        If you see output, it's loaded. If not, proceed to the next step.

    *   **Load manually (temporary):**
        ```bash
        sudo modprobe uinput
        ```
        If this command fails, your kernel might not have `uinput` support compiled in, or the module name is different (unlikely for standard distributions).

    *   **Ensure it loads at boot (permanent):**
        Create a file to tell the system to load `uinput` on startup:
        ```bash
        echo uinput | sudo tee /etc/modules-load.d/uinput.conf
        ```
        You might need to reboot or run `sudo systemctl restart systemd-modules-load.service` for this to take immediate effect without a full reboot.

4.  **Permissions for `/dev/uinput` (to run without `sudo`):**
    By default, accessing `/dev/uinput` requires root privileges. To run the script as a regular user, you need to set up a `udev` rule and add your user to the appropriate group.

    *   **Create a udev rule:**
        Create a file named `/etc/udev/rules.d/99-uinput.rules` (you can use `sudo nano` or your preferred editor):
        ```bash
        sudo nano /etc/udev/rules.d/99-uinput.rules
        ```
        Add the following content to the file:
        ```
        KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
        ```
        Save and close the file. The group might sometimes be `uinput` on some systems; `input` is common.

    *   **Add your user to the `input` group:**
        (Replace `$USER` with your actual username if it's not automatically expanded by your shell when using `sudo`).
        ```bash
        sudo usermod -aG input $USER
        ```

    *   **Apply changes:**
        You **must reboot your system or re-login** for the group membership and udev rule changes to take full effect. After this, you should be able to run the script without `sudo`.

## Finding Controller Information

You'll need either the controller's name or its MAC address, and the UUID for its sensor data characteristic.

*   **MAC Address & Name:**
    1.  Put your Daydream controller in pairing mode (usually by holding the Home button until the LED blinks).
    2.  Use `bluetoothctl`:
        ```bash
        bluetoothctl
        scan on
        # Wait for your "Daydream controller" to appear, note its MAC address.
        # Example output: [NEW] Device 54:AB:3A:F2:ED:07 Daydream controller
        scan off
        exit
        ```

*   **Sensor Characteristic UUID:**
    For Daydream controllers, the main sensor data (touch, buttons, orientation) characteristic UUID is almost always:
    `00000001-1000-1000-8000-00805f9b34fb`
    This is the UUID you'll pass as an argument to the script.

## Usage

Wake the controller (press the Home button) and run:

```bash
daydream
```

With no arguments it connects to the device named `Daydream controller` using the standard sensor characteristic UUID. Press `Ctrl+C` to stop.

**Command Structure:**
```bash
daydream [OPTIONS] [--name <NAME> | --address <ADDRESS>] [SENSOR_CHARACTERISTIC_UUID]
```

**Examples:**

*   **Connecting by MAC Address:**
    (Replace `54:AB:3A:F2:ED:07` with your controller's actual MAC address)
    ```bash
    daydream --address 54:AB:3A:F2:ED:07
    ```

*   **With custom mouse sensitivity and debug logging:**
    ```bash
    daydream --sensitivity-x 1500 --sensitivity-y 1200 --debug
    ```

*   **With custom key mappings** (e.g. App button → `Enter`, Volume+ → `F5`):
    ```bash
    daydream --map-app KEY_ENTER --map-vol-plus KEY_F5
    ```

*   **Plain log output** (e.g. for a log file or systemd; this also happens automatically when output isn't a terminal):
    ```bash
    daydream --no-ui
    ```

*   **Running from the source checkout** (without installing):
    ```bash
    uv run daydream
    ```

*   **Running with `sudo`** (only if the udev rule isn't set up; the udev rule is the better fix):
    ```bash
    sudo ~/.local/bin/daydream
    ```

### Status Bar

When run in a terminal, the bottom of the screen shows a live status line:

```
────────────────────────────────────────────────────────────────────────────────────────
 ● CONNECTED  Daydream controller 54:AB:3A:F2:ED:07 │ ▰▰▰▰▱ 87% │  61 Hz   connected 12:31
  CLICK  APP  HOME  VOL−  VOL+  │ touch 0.42, 0.61 │ last VOL+ → Right 3s ago
```

*   **State badge:** `SCANNING` / `CONNECTING` / `CONNECTED` / `RECONNECTING` / `ERROR`, with a spinner while it's waiting.
*   **Battery** level and **packet rate** (around 60 Hz when the link is healthy).
*   **Buttons** light up while held, plus the current **touch** position and the **last action** sent.
*   While not connected, the second line says what it's doing (e.g. which scan attempt it's on).

### Command-Line Arguments:

**Device selection (all optional):**
*   `--name <NAME>`: The Bluetooth name of your Daydream controller. Default: `Daydream controller`.
*   `--address <ADDRESS>`: The MAC address of your Daydream controller (use instead of `--name`).
*   `SENSOR_CHARACTERISTIC_UUID`: (Positional) The controller's sensor data characteristic. Default: `00000001-1000-1000-8000-00805f9b34fb`.

**Mouse / touch tuning:**
*   `--sensitivity-x <VALUE>`: Mouse X-axis sensitivity. Default: 1000.
*   `--sensitivity-y <VALUE>`: Mouse Y-axis sensitivity. Default: 900.
*   `--dead-zone <VALUE>`: Touch dead-zone threshold below which movement is ignored. Default: 0.01.
*   `--acceleration`: Enable pointer acceleration.
*   `--acceleration-factor <VALUE>`: Acceleration multiplier. Default: 1.5.
*   `--acceleration-threshold <VALUE>`: Movement threshold to trigger acceleration. Default: 0.03.

**Key mappings:** *(remap a button to a different key)*
*   `--map-click <KEY>`: Key for the touchpad click. Default: `BTN_LEFT`.
*   `--map-vol-plus <KEY>`: Key for the volume-plus button. Default: `KEY_RIGHT`.
*   `--map-vol-minus <KEY>`: Key for the volume-minus button. Default: `KEY_LEFT`.
*   `--map-app <KEY>`: Key for the app button. Default: `KEY_ESC`.
*   `--map-home <KEY>`: Key for the home button. Default: `KEY_LEFTMETA`.

    Valid `<KEY>` values: `BTN_LEFT`, `KEY_DOWN`, `KEY_ENTER`, `KEY_ESC`, `KEY_F5`, `KEY_F11`, `KEY_LEFT`, `KEY_LEFTMETA`, `KEY_RIGHT`, `KEY_SPACE`, `KEY_UP`.

**Other:**
*   `--parse-imu`: Parse IMU data (orientation, accelerometer, gyroscope). Off by default for performance.
*   `--macos-use-bdaddr`: When true, use Bluetooth address instead of UUID on macOS for device identification (less relevant for this Linux-focused uinput script).
*   `--no-ui`: Plain log lines instead of the live status bar (automatic when output isn't a terminal).
*   `-d`, `--debug`: Enable debug logging for more verbose output.

## Troubleshooting

*   **"Failed to create uinput device: [Errno 19] No such device"**:
    This means the `/dev/uinput` device node is missing.
    1.  Ensure the `uinput` kernel module is loaded: `sudo modprobe uinput`.
    2.  Make it load at boot (see Setup section).
*   **"Failed to create uinput device: [Errno 13] Permission denied"**:
    You don't have permission to access `/dev/uinput`.
    1.  Run with `sudo ~/.local/bin/daydream`.
    2.  OR, correctly set up the `udev` rule and add your user to the `input` group, then re-login/reboot (see Setup section).
*   **Cannot find device / Connection issues:**
    1.  Ensure your Daydream controller is charged and in pairing mode or turned on.
    2.  Verify your system's Bluetooth is enabled and working.
    3.  Double-check the controller's name or MAC address.
    4.  Try bringing the controller closer to your Bluetooth adapter.
    5.  Use the `--debug` flag for more detailed connection logs.
*   **Mouse/Keyboard input not working in specific applications on Wayland:**
    `uinput` generally provides good compatibility. If an XWayland application is not receiving input, ensure it has focus. Native Wayland applications should work correctly.

## Development

```bash
uv sync                     # create .venv with Python 3.14 + dev tools
uv run pytest -v            # run the tests (no hardware or /dev/uinput needed)
uv run ruff check daydream.py tests/
uv run ruff format daydream.py tests/
```

## License

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
