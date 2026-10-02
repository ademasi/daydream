"""Tests for the live status model and the terminal status bar."""

import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from daydream import (
    BridgeStatus,
    DaydreamBridge,
    Phase,
    StatusLine,
    key_label,
    setup_battery_monitoring,
)

uinput = sys.modules["uinput"]


def make_packet(*, x_touch_raw=0, y_touch_raw=0, buttons=0) -> bytearray:
    data = bytearray(19)
    data[16] = (x_touch_raw >> 3) & 0x1F
    data[17] = ((x_touch_raw & 0x07) << 5) | ((y_touch_raw >> 3) & 0x1F)
    data[18] = ((y_touch_raw & 0x07) << 5) | buttons
    return data


def render_text(status: BridgeStatus, width: int = 120) -> str:
    console = Console(width=width, record=True, color_system=None, file=MagicMock())
    console.print(StatusLine(status).render())
    return console.export_text()


class TestKeyLabel:
    def test_known_keys(self):
        assert key_label(uinput.KEY_RIGHT) == "Right"
        assert key_label(uinput.KEY_ESC) == "Esc"

    def test_unknown_event_falls_back_to_repr(self):
        assert key_label((9, 9)) == "(9, 9)"


class TestBridgeStatusUpdates:
    def test_packets_counted_and_buttons_held(self, bridge):
        bridge.sensor_notification_handler(MagicMock(), make_packet(buttons=0x10 | 0x01))
        assert bridge.status.packets == 1
        assert bridge.status.held == frozenset({"VOL+", "CLICK"})

    def test_invalid_packet_not_counted(self, bridge):
        bridge.sensor_notification_handler(MagicMock(), bytearray(5))
        assert bridge.status.packets == 0

    def test_touch_position_tracked(self, bridge):
        bridge.sensor_notification_handler(
            MagicMock(), make_packet(x_touch_raw=128, y_touch_raw=64)
        )
        x, y = bridge.status.touch
        assert x == pytest.approx(128 / 255)
        assert y == pytest.approx(64 / 255)
        bridge.sensor_notification_handler(MagicMock(), make_packet())
        assert bridge.status.touch is None

    def test_button_press_records_last_action(self, bridge):
        bridge.sensor_notification_handler(MagicMock(), make_packet())
        bridge.sensor_notification_handler(MagicMock(), make_packet(buttons=0x10))
        assert bridge.status.last_action == "VOL+ → Right"
        assert bridge.status.last_action_at > 0

    def test_battery_callback_updates_status(self):
        b = DaydreamBridge()
        b._on_battery(80)
        assert b.status.battery == 80


class TestBatteryMonitoring:
    @pytest.mark.asyncio
    async def test_notify_reports_initial_level(self):
        char = MagicMock(uuid="00002a19-0000-1000-8000-00805f9b34fb", properties=["notify"])
        service = MagicMock(uuid="0000180f-0000-1000-8000-00805f9b34fb", characteristics=[char])
        client = MagicMock(services=[service])
        client.start_notify = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=bytearray([73]))
        levels = []

        task = await setup_battery_monitoring(client, levels.append)

        assert task is None
        assert levels == [73]
        client.start_notify.assert_awaited_once()


class TestStatusLine:
    def test_renders_scanning_detail(self):
        status = BridgeStatus(target="Daydream controller")
        status.set_phase(Phase.SCANNING, "looking for 'Daydream controller' (attempt 1/3)")
        out = render_text(status)
        assert "SCANNING" in out
        assert "attempt 1/3" in out

    def test_renders_connected_state(self):
        status = BridgeStatus(
            phase=Phase.CONNECTED,
            device_name="Daydream controller",
            device_address="54:AB:3A:F2:ED:07",
            battery=87,
            held=frozenset({"VOL+"}),
            touch=(0.42, 0.61),
            last_action="VOL+ → Right",
            last_action_at=time.monotonic(),
            connected_at=time.monotonic(),
        )
        out = render_text(status)
        assert "CONNECTED" in out
        assert "54:AB:3A:F2:ED:07" in out
        assert "87%" in out
        assert "0.42, 0.61" in out
        assert "VOL+ → Right" in out
        assert "connected 00:00" in out

    def test_narrow_terminal_does_not_wrap(self):
        status = BridgeStatus(phase=Phase.CONNECTED, device_name="Daydream controller")
        out = render_text(status, width=40)
        assert len(out.rstrip("\n").splitlines()) == 3


class TestEmptyBatteryPayload:
    """The real controller sometimes sends zero-length battery values."""

    @pytest.mark.asyncio
    async def test_empty_initial_read_is_ignored(self):
        char = MagicMock(uuid="00002a19-0000-1000-8000-00805f9b34fb", properties=["notify"])
        service = MagicMock(uuid="0000180f-0000-1000-8000-00805f9b34fb", characteristics=[char])
        client = MagicMock(services=[service])
        client.start_notify = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=bytearray())
        levels = []

        await setup_battery_monitoring(client, levels.append)

        assert levels == []
        client.start_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_notification_is_ignored(self):
        char = MagicMock(uuid="00002a19-0000-1000-8000-00805f9b34fb", properties=["notify"])
        service = MagicMock(uuid="0000180f-0000-1000-8000-00805f9b34fb", characteristics=[char])
        client = MagicMock(services=[service])
        client.start_notify = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=bytearray([50]))
        levels = []

        await setup_battery_monitoring(client, levels.append)
        notify_callback = client.start_notify.await_args.args[1]
        notify_callback(char, bytearray())
        notify_callback(char, bytearray([61]))

        assert levels == [50, 61]
