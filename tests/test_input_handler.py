"""Tests for DaydreamBridge input handling (touch movement and button events)."""

import sys
from unittest.mock import MagicMock

from daydream import DaydreamBridge, KeyMap, TouchConfig

uinput = sys.modules["uinput"]


def make_packet(
    *,
    click=False,
    app=False,
    home=False,
    vol_plus=False,
    vol_minus=False,
    x_touch_raw=0,
    y_touch_raw=0,
) -> bytearray:
    """Construct a 19-byte test packet."""
    data = bytearray(19)
    data[16] = (data[16] & 0xE0) | ((x_touch_raw >> 3) & 0x1F)
    data[17] = ((x_touch_raw & 0x07) << 5) | ((y_touch_raw >> 3) & 0x1F)
    data[18] = (y_touch_raw & 0x07) << 5
    if click:
        data[18] |= 0x01
    if home:
        data[18] |= 0x02
    if app:
        data[18] |= 0x04
    if vol_minus:
        data[18] |= 0x08
    if vol_plus:
        data[18] |= 0x10
    return data


def _send(bridge, packet):
    """Helper to send a packet through the bridge's notification handler."""
    bridge.sensor_notification_handler(MagicMock(), packet)


class TestTouchMovement:
    def test_touch_emits_rel_xy(self, bridge):
        """Moving finger on touchpad should emit REL_X and REL_Y."""
        # First packet initializes state
        _send(bridge, make_packet(x_touch_raw=128, y_touch_raw=128))
        # Second packet — still on pad, same position, sets up tracking
        _send(bridge, make_packet(x_touch_raw=128, y_touch_raw=128))
        bridge.mock_uinput_device = bridge.uinput_device
        bridge.uinput_device.reset_mock()

        # Third packet — move finger significantly
        _send(bridge, make_packet(x_touch_raw=200, y_touch_raw=200))
        # Should have emitted REL_X and REL_Y
        emit_calls = bridge.uinput_device.emit.call_args_list
        events_emitted = [c[0][0] for c in emit_calls]
        assert uinput.REL_X in events_emitted
        assert uinput.REL_Y in events_emitted
        bridge.uinput_device.syn.assert_called()

    def test_finger_lift_resets_tracking(self, bridge):
        """Lifting finger (touch=0,0) and putting it back shouldn't jump."""
        # Put finger down
        _send(bridge, make_packet(x_touch_raw=100, y_touch_raw=100))
        _send(bridge, make_packet(x_touch_raw=100, y_touch_raw=100))
        # Lift finger
        _send(bridge, make_packet(x_touch_raw=0, y_touch_raw=0))
        bridge.uinput_device.reset_mock()
        # Put finger down at a totally different spot — should NOT emit movement
        _send(bridge, make_packet(x_touch_raw=200, y_touch_raw=200))
        bridge.uinput_device.emit.assert_not_called()

    def test_dead_zone_filters_small_movements(self, bridge):
        """Very small movements should be filtered by dead zone."""
        bridge.touch_config = TouchConfig(sensitivity_x=1000, sensitivity_y=900, dead_zone=100.0)
        _send(bridge, make_packet(x_touch_raw=128, y_touch_raw=128))
        _send(bridge, make_packet(x_touch_raw=128, y_touch_raw=128))
        bridge.uinput_device.reset_mock()
        # Tiny movement — 1 raw unit = ~0.004 normalized, *1000 = ~3.9 < 100 dead zone
        _send(bridge, make_packet(x_touch_raw=129, y_touch_raw=129))
        bridge.uinput_device.emit.assert_not_called()

    def test_acceleration_amplifies_large_movements(self):
        """With acceleration enabled, large movements should be amplified."""
        tc = TouchConfig(
            sensitivity_x=1000,
            sensitivity_y=900,
            dead_zone=0.01,
            acceleration=True,
            acceleration_factor=2.0,
            acceleration_threshold=0.01,
        )
        bridge_accel = DaydreamBridge(touch_config=tc)
        bridge_accel.uinput_device = MagicMock()

        _send(bridge_accel, make_packet(x_touch_raw=50, y_touch_raw=50))
        _send(bridge_accel, make_packet(x_touch_raw=50, y_touch_raw=50))
        bridge_accel.uinput_device.reset_mock()

        # Large movement
        _send(bridge_accel, make_packet(x_touch_raw=200, y_touch_raw=200))
        emit_calls = bridge_accel.uinput_device.emit.call_args_list
        # Check that at least one emission happened
        assert len(emit_calls) > 0

        # Compare with non-accelerated bridge
        bridge_plain = DaydreamBridge(
            touch_config=TouchConfig(sensitivity_x=1000, sensitivity_y=900)
        )
        bridge_plain.uinput_device = MagicMock()
        _send(bridge_plain, make_packet(x_touch_raw=50, y_touch_raw=50))
        _send(bridge_plain, make_packet(x_touch_raw=50, y_touch_raw=50))
        bridge_plain.uinput_device.reset_mock()
        _send(bridge_plain, make_packet(x_touch_raw=200, y_touch_raw=200))

        # Accelerated values should be larger
        accel_x = emit_calls[0][0][1]
        plain_x = bridge_plain.uinput_device.emit.call_args_list[0][0][1]
        assert abs(accel_x) > abs(plain_x)


class TestButtonEvents:
    def test_click_press_release(self, bridge):
        """Click should emit press (1) then release (0) on edges."""
        _send(bridge, make_packet())  # init
        bridge.uinput_device.reset_mock()

        # Press click
        _send(bridge, make_packet(click=True))
        bridge.uinput_device.emit.assert_any_call(uinput.BTN_LEFT, 1)

        bridge.uinput_device.reset_mock()
        # Release click
        _send(bridge, make_packet(click=False))
        bridge.uinput_device.emit.assert_any_call(uinput.BTN_LEFT, 0)

    def test_sustained_press_no_reemit(self, bridge):
        """Holding a button should not re-emit on every packet."""
        _send(bridge, make_packet())  # init
        _send(bridge, make_packet(vol_plus=True))  # rising edge
        bridge.uinput_device.reset_mock()
        _send(bridge, make_packet(vol_plus=True))  # sustained
        bridge.uinput_device.emit_click.assert_not_called()

    def test_vol_plus_emits_click(self, bridge):
        _send(bridge, make_packet())
        bridge.uinput_device.reset_mock()
        _send(bridge, make_packet(vol_plus=True))
        bridge.uinput_device.emit_click.assert_called_with(uinput.KEY_RIGHT)

    def test_vol_minus_emits_click(self, bridge):
        _send(bridge, make_packet())
        bridge.uinput_device.reset_mock()
        _send(bridge, make_packet(vol_minus=True))
        bridge.uinput_device.emit_click.assert_called_with(uinput.KEY_LEFT)

    def test_app_emits_click(self, bridge):
        _send(bridge, make_packet())
        bridge.uinput_device.reset_mock()
        _send(bridge, make_packet(app=True))
        bridge.uinput_device.emit_click.assert_called_with(uinput.KEY_ESC)

    def test_home_emits_click(self, bridge):
        _send(bridge, make_packet())
        bridge.uinput_device.reset_mock()
        _send(bridge, make_packet(home=True))
        bridge.uinput_device.emit_click.assert_called_with(uinput.KEY_LEFTMETA)

    def test_custom_keymap(self):
        """Custom key mappings should be used for button events."""
        km = KeyMap(
            click=uinput.KEY_SPACE,
            vol_plus=uinput.KEY_F5,
            app=uinput.KEY_ENTER,
        )
        b = DaydreamBridge(key_map=km)
        b.uinput_device = MagicMock()

        _send(b, make_packet())
        b.uinput_device.reset_mock()

        _send(b, make_packet(click=True))
        b.uinput_device.emit.assert_any_call(uinput.KEY_SPACE, 1)

        b.uinput_device.reset_mock()
        _send(b, make_packet(vol_plus=True, click=True))
        b.uinput_device.emit_click.assert_called_with(uinput.KEY_F5)

        b.uinput_device.reset_mock()
        _send(b, make_packet(app=True))
        b.uinput_device.emit_click.assert_called_with(uinput.KEY_ENTER)
