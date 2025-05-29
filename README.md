# Daydream Remote Control for Linux (using uinput)

This Python script allows you to use a Google Daydream controller as a mouse and keyboard on Linux systems, particularly well-suited for Wayland compositors like Sway due to its use of `python-uinput`.

It translates touchpad movements and button presses from the Daydream controller into system-level input events, enabling mouse control, presentation navigation, and common keyboard shortcuts. It also attempts to read and report the controller's battery level and device information.

This was made to use this controler for keynote presentations (e.g., with pympress).

## Features

*   **Mouse Control:**
    *   Touchpad area moves the mouse cursor.
    *   Physical click on the touchpad simulates a left mouse button click.
*   **Keyboard Emulation:**
    *   **Volume Plus (+)**: Emulates `Right Arrow` (next slide).
    *   **Volume Minus (-)**: Emulates `Left Arrow` (previous slide).
    *   **App Button** (circle/minus icon): Emulates `Escape` key.
    *   **Home Button** (bottom-most): Emulates `Super/Meta/Windows` key.
*   **Device Information:**
    *   Reads and logs manufacturer, model number, etc., if available via the standard Device Information Service.
*   **Battery Reporting:**
    *   Monitors and logs battery level, using notifications if supported, or periodic reads as a fallback.
*   **Sway/Wayland Compatibility:** Creates a virtual input device via `uinput`, ensuring good compatibility.
*   **Configurable Sensitivity:** Mouse sensitivity can be adjusted via command-line arguments.

## Prerequisites

### Hardware
*   A Google Daydream controller.
*   A Bluetooth adapter on your Linux machine.

### Software
*   Python 3.8+
*   `pip` (Python package installer)
*   `uinput` kernel module (see Setup section)
*   Required Python libraries: `bleak`, `python-uinput`
*   System dependencies for `python-uinput` (these might vary slightly by distribution):
    *   On Debian/Ubuntu-based systems:
        ```bash
        sudo apt-get update
        sudo apt-get install linux-headers-$(uname -r) libudev-dev python3-dev build-essential
        ```

## Setup Instructions

1.  **Clone or Download the Script:**
    Obtain the `daydreamv2.py` (or your script name) file.
    ```bash
     git https://github.com/ademasi/daydream
     cd daydream
    ```

2.  **Install Python Dependencies:**
    ```bash
    uv pip install bleak python-uinput
    ```


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
        # Example output: [NEW] Device AA:BB:CC:DD:EE:FF Daydream controller
        scan off
        exit
        ```

*   **Sensor Characteristic UUID:**
    For Daydream controllers, the main sensor data (touch, buttons, orientation) characteristic UUID is almost always:
    `00000001-1000-1000-8000-00805f9b34fb`
    This is the UUID you'll pass as an argument to the script.

## Usage

Run the script from your terminal.

**Command Structure:**
```bash
python daydreamv2.py [OPTIONS] {--name <NAME> | --address <ADDRESS>} <SENSOR_CHARACTERISTIC_UUID>
```

**Examples:**

*   **Connecting by Name (recommended if udev rules are set):**
    ```bash
    python daydreamv2.py --name "Daydream controller" 00000001-1000-1000-8000-00805f9b34fb
    ```

*   **Connecting by MAC Address (if udev rules are set):**
    (Replace `AA:BB:CC:DD:EE:FF` with your controller's actual MAC address)
    ```bash
    python daydreamv2.py --address AA:BB:CC:DD:EE:FF 00000001-1000-1000-8000-00805f9b34fb
    ```

*   **Running with `sudo` (if udev rules are not set up or not working):**
    ```bash
    sudo python daydreamv2.py --name "Daydream controller" 00000001-1000-1000-8000-00805f9b34fb
    ```

*   **With custom mouse sensitivity and debug logging:**
    ```bash
    python daydreamv2.py --name "Daydream controller" --sensitivity-x 1500 --sensitivity-y 1200 --debug 00000001-1000-1000-8000-00805f9b34fb
    ```

**To stop the script, press `Ctrl+C` in the terminal where it's running.**

### Command-Line Arguments:

*   `--name <NAME>`: The Bluetooth name of your Daydream controller.
*   `--address <ADDRESS>`: The MAC address of your Daydream controller.
    *(You must provide either `--name` or `--address`)*
*   `SENSOR_CHARACTERISTIC_UUID`: (Positional argument) The UUID for the controller's main sensor data characteristic (typically `00000001-1000-1000-8000-00805f9b34fb`).
*   `--macos-use-bdaddr`: (Optional) When true, use Bluetooth address instead of UUID on macOS for device identification (less relevant for this Linux-focused uinput script).
*   `--sensitivity-x <VALUE>`: (Optional) Mouse X-axis sensitivity. Default: 1000.
*   `--sensitivity-y <VALUE>`: (Optional) Mouse Y-axis sensitivity. Default: 900.
*   `-d`, `--debug`: (Optional) Enable debug logging for more verbose output.

## Troubleshooting

*   **"Failed to create uinput device: [Errno 19] No such device"**:
    This means the `/dev/uinput` device node is missing.
    1.  Ensure the `uinput` kernel module is loaded: `sudo modprobe uinput`.
    2.  Make it load at boot (see Setup section).
*   **"Failed to create uinput device: [Errno 13] Permission denied"**:
    You don't have permission to access `/dev/uinput`.
    1.  Run the script with `sudo`.
    2.  OR, correctly set up the `udev` rule and add your user to the `input` group, then re-login/reboot (see Setup section).
*   **Cannot find device / Connection issues:**
    1.  Ensure your Daydream controller is charged and in pairing mode or turned on.
    2.  Verify your system's Bluetooth is enabled and working.
    3.  Double-check the controller's name or MAC address.
    4.  Try bringing the controller closer to your Bluetooth adapter.
    5.  Use the `--debug` flag for more detailed connection logs.
*   **Mouse/Keyboard input not working in specific applications on Wayland:**
    `uinput` generally provides good compatibility. If an XWayland application is not receiving input, ensure it has focus. Native Wayland applications should work correctly.

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
