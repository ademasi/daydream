"""Tests for the State packet parser."""

import pytest

from daydream import State


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
    """Construct a 19-byte test packet with specified button/touch values.

    Bytes 0-15 are zeroed (timestamp, sequence, IMU).
    Bytes 16-17 carry touch data.
    Byte 18 carries button bitmask.
    """
    data = bytearray(19)
    # Touch: x_touch_raw in bits [16][4:0] << 3 | [17][7:5]
    data[16] = (data[16] & 0xE0) | ((x_touch_raw >> 3) & 0x1F)
    data[17] = ((x_touch_raw & 0x07) << 5) | ((y_touch_raw >> 3) & 0x1F)
    data[18] = (y_touch_raw & 0x07) << 5
    # Buttons on byte 18
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


class TestStateValid:
    def test_valid_packet(self):
        data = make_packet()
        s = State(data)
        assert s.valid is True

    def test_short_packet_invalid(self):
        data = bytearray(10)
        s = State(data)
        assert s.valid is False
        assert s.isClickDown is False


class TestStateButtons:
    def test_no_buttons(self):
        s = State(make_packet())
        assert s.isClickDown is False
        assert s.isAppDown is False
        assert s.isHomeDown is False
        assert s.isVolPlusDown is False
        assert s.isVolMinusDown is False

    def test_click_only(self):
        s = State(make_packet(click=True))
        assert s.isClickDown is True
        assert s.isAppDown is False

    def test_app_only(self):
        s = State(make_packet(app=True))
        assert s.isAppDown is True
        assert s.isClickDown is False

    def test_home_only(self):
        s = State(make_packet(home=True))
        assert s.isHomeDown is True

    def test_vol_plus_only(self):
        s = State(make_packet(vol_plus=True))
        assert s.isVolPlusDown is True

    def test_vol_minus_only(self):
        s = State(make_packet(vol_minus=True))
        assert s.isVolMinusDown is True

    def test_multiple_buttons(self):
        s = State(make_packet(click=True, app=True, vol_plus=True))
        assert s.isClickDown is True
        assert s.isAppDown is True
        assert s.isVolPlusDown is True
        assert s.isHomeDown is False
        assert s.isVolMinusDown is False


class TestStateTouch:
    def test_zero_means_no_contact(self):
        s = State(make_packet(x_touch_raw=0, y_touch_raw=0))
        assert s.x_touch == 0.0
        assert s.y_touch == 0.0

    def test_max_touch(self):
        s = State(make_packet(x_touch_raw=255, y_touch_raw=255))
        assert s.x_touch == pytest.approx(1.0)
        assert s.y_touch == pytest.approx(1.0)

    def test_mid_touch(self):
        s = State(make_packet(x_touch_raw=128, y_touch_raw=128))
        assert s.x_touch == pytest.approx(128 / 255.0)
        assert s.y_touch == pytest.approx(128 / 255.0)


class TestStateIMU:
    def test_imu_parsed_by_default(self):
        s = State(make_packet())
        assert hasattr(s, "xOri")
        assert hasattr(s, "xAcc")
        assert hasattr(s, "xGyro")

    def test_imu_skipped_when_disabled(self):
        s = State(make_packet(), parse_imu=False)
        assert s.xOri is None
        assert s.xAcc is None
        assert s.xGyro is None
        # But touch/buttons still work
        assert s.valid is True
