"""Tests for DaydreamBridge lifecycle — reconnect, cleanup, state reset."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from daydream import DaydreamBridge, KeyMap, TouchConfig


def make_packet(*, x_touch_raw=0, y_touch_raw=0, click=False) -> bytearray:
    data = bytearray(19)
    data[16] = (data[16] & 0xE0) | ((x_touch_raw >> 3) & 0x1F)
    data[17] = ((x_touch_raw & 0x07) << 5) | ((y_touch_raw >> 3) & 0x1F)
    data[18] = (y_touch_raw & 0x07) << 5
    if click:
        data[18] |= 0x01
    return data


class TestBridgeLifecycle:
    def test_init_defaults(self):
        b = DaydreamBridge()
        assert isinstance(b.key_map, KeyMap)
        assert isinstance(b.touch_config, TouchConfig)
        assert b.uinput_device is None
        assert b.parse_imu is True

    def test_reset_handler_state(self, bridge):
        """_reset_handler_state should clear all tracking state."""
        # Dirty the state
        bridge._handler_state["prev_state"] = "something"
        bridge._handler_state["finger_on_pad"] = True
        bridge._handler_state["last_touch_x"] = 0.5

        bridge._reset_handler_state()

        assert bridge._handler_state["prev_state"] is None
        assert bridge._handler_state["finger_on_pad"] is False
        assert bridge._handler_state["last_touch_x"] == 0.0
        assert bridge._handler_state["last_touch_y"] == 0.0

    def test_destroy_uinput(self, bridge):
        """destroy_uinput should call destroy and set device to None."""
        device = bridge.uinput_device
        bridge.destroy_uinput()
        device.destroy.assert_called_once()
        assert bridge.uinput_device is None

    def test_destroy_uinput_when_none(self):
        """destroy_uinput with no device should not raise."""
        b = DaydreamBridge()
        b.destroy_uinput()  # Should not raise

    def test_notification_without_device(self, bridge):
        """Notifications before uinput init should be silently skipped."""
        bridge.uinput_device = None
        # Should not raise
        bridge.sensor_notification_handler(MagicMock(), make_packet())


class TestBridgeReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_resets_handler_state(self):
        """After disconnect, handler state should be reset on reconnect."""
        bridge = DaydreamBridge()
        bridge.uinput_device = MagicMock()

        # Simulate some state
        bridge._handler_state["prev_state"] = "old"
        bridge._handler_state["finger_on_pad"] = True

        # Mock a client: connected initially, then disconnects on polling
        mock_client = AsyncMock()
        is_connected_values = [True, True, True, False, False]
        type(mock_client).is_connected = PropertyMock(side_effect=is_connected_values)
        mock_client.services = []
        mock_client.start_notify = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        cli_args = MagicMock()
        cli_args.characteristic = "test-uuid"

        with (
            patch("daydream.BleakClient", return_value=mock_client),
            patch("daydream.read_device_info", new_callable=AsyncMock),
            patch(
                "daydream.setup_battery_monitoring",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await bridge._connect_and_run(MagicMock(), cli_args)

        # Handler state should have been reset at the start of _connect_and_run
        assert bridge._handler_state["prev_state"] is None
        assert bridge._handler_state["finger_on_pad"] is False

    @pytest.mark.asyncio
    async def test_run_cleanup_on_keyboard_interrupt(self):
        """KeyboardInterrupt should trigger cleanup and destroy uinput."""
        bridge = DaydreamBridge()
        mock_device = MagicMock()

        cli_args = MagicMock()
        cli_args.name = "test"
        cli_args.address = None
        cli_args.characteristic = "uuid"
        cli_args.macos_use_bdaddr = False

        with patch.object(bridge, "init_uinput"):
            bridge.uinput_device = mock_device
            with patch.object(
                bridge,
                "_find_device",
                new_callable=AsyncMock,
                side_effect=KeyboardInterrupt,
            ):
                await bridge.run(cli_args)

        mock_device.destroy.assert_called_once()
        assert bridge.uinput_device is None


class TestBridgeShutdown:
    @pytest.mark.asyncio
    async def test_cancel_while_connected_disconnects_controller(self):
        """Ctrl+C (task cancellation) while connected must drop the BLE link."""
        import asyncio

        bridge = DaydreamBridge()
        bridge.uinput_device = MagicMock()

        mock_client = AsyncMock()
        type(mock_client).is_connected = PropertyMock(return_value=True)
        mock_client.services = []
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        device = MagicMock(address="AA:BB", name="Daydream controller")
        cli_args = MagicMock(characteristic="uuid")

        with (
            patch("daydream.BleakClient", return_value=mock_client),
            patch("daydream.read_device_info", new_callable=AsyncMock, return_value={}),
            patch("daydream.setup_battery_monitoring", new_callable=AsyncMock, return_value=None),
        ):
            task = asyncio.create_task(bridge._connect_and_run(device, cli_args))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        mock_client.disconnect.assert_awaited_once()
