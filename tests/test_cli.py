"""Tests for CLI argument parsing — backwards compatibility and new flags."""

import sys

import pytest

from daydream import DEFAULT_DEVICE_NAME, DEFAULT_SENSOR_UUID, parse_args

uinput = sys.modules["uinput"]


class TestCLIBackwardsCompat:
    def test_name_and_characteristic(self):
        args = parse_args(["--name", "Daydream controller", "some-uuid"])
        assert args.name == "Daydream controller"
        assert args.characteristic == "some-uuid"
        assert args.address is None

    def test_address_and_characteristic(self):
        args = parse_args(["--address", "AA:BB:CC:DD:EE:FF", "some-uuid"])
        assert args.address == "AA:BB:CC:DD:EE:FF"
        assert args.name is None

    def test_name_and_address_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            parse_args(["--name", "foo", "--address", "bar", "uuid"])

    def test_characteristic_defaults_to_sensor_uuid(self):
        args = parse_args(["--name", "foo"])
        assert args.characteristic == DEFAULT_SENSOR_UUID

    def test_device_defaults_to_name(self):
        args = parse_args(["some-uuid"])
        assert args.name == DEFAULT_DEVICE_NAME
        assert args.characteristic == "some-uuid"

    def test_no_args_uses_defaults(self):
        args = parse_args([])
        assert args.name == DEFAULT_DEVICE_NAME
        assert args.address is None
        assert args.characteristic == DEFAULT_SENSOR_UUID

    def test_address_does_not_get_default_name(self):
        args = parse_args(["--address", "AA:BB:CC:DD:EE:FF"])
        assert args.name is None

    def test_sensitivity_defaults(self):
        args = parse_args(["--name", "test", "uuid"])
        assert args.sensitivity_x == 1000
        assert args.sensitivity_y == 900

    def test_sensitivity_custom(self):
        args = parse_args(
            ["--name", "test", "--sensitivity-x", "500", "--sensitivity-y", "400", "uuid"]
        )
        assert args.sensitivity_x == 500
        assert args.sensitivity_y == 400

    def test_debug_flag(self):
        args = parse_args(["--name", "test", "-d", "uuid"])
        assert args.debug is True

    def test_debug_long_flag(self):
        args = parse_args(["--name", "test", "--debug", "uuid"])
        assert args.debug is True

    def test_macos_bdaddr_flag(self):
        args = parse_args(["--name", "test", "--macos-use-bdaddr", "uuid"])
        assert args.macos_use_bdaddr is True


class TestCLINewFlags:
    def test_map_click(self):
        args = parse_args(["--name", "test", "--map-click", "KEY_SPACE", "uuid"])
        assert args.map_click == "KEY_SPACE"

    def test_map_vol_plus(self):
        args = parse_args(["--name", "test", "--map-vol-plus", "KEY_F5", "uuid"])
        assert args.map_vol_plus == "KEY_F5"

    def test_map_vol_minus(self):
        args = parse_args(["--name", "test", "--map-vol-minus", "KEY_DOWN", "uuid"])
        assert args.map_vol_minus == "KEY_DOWN"

    def test_map_app(self):
        args = parse_args(["--name", "test", "--map-app", "KEY_ENTER", "uuid"])
        assert args.map_app == "KEY_ENTER"

    def test_map_home(self):
        args = parse_args(["--name", "test", "--map-home", "KEY_F11", "uuid"])
        assert args.map_home == "KEY_F11"

    def test_invalid_key_name_rejected(self):
        with pytest.raises(SystemExit):
            parse_args(["--name", "test", "--map-click", "INVALID_KEY", "uuid"])

    def test_dead_zone_default(self):
        args = parse_args(["--name", "test", "uuid"])
        assert args.dead_zone == 0.01

    def test_dead_zone_custom(self):
        args = parse_args(["--name", "test", "--dead-zone", "0.05", "uuid"])
        assert args.dead_zone == 0.05

    def test_acceleration_off_by_default(self):
        args = parse_args(["--name", "test", "uuid"])
        assert args.acceleration is False

    def test_acceleration_flag(self):
        args = parse_args(["--name", "test", "--acceleration", "uuid"])
        assert args.acceleration is True

    def test_acceleration_factor(self):
        args = parse_args(["--name", "test", "--acceleration-factor", "2.0", "uuid"])
        assert args.acceleration_factor == 2.0

    def test_acceleration_threshold(self):
        args = parse_args(["--name", "test", "--acceleration-threshold", "0.05", "uuid"])
        assert args.acceleration_threshold == 0.05

    def test_parse_imu_off_by_default(self):
        args = parse_args(["--name", "test", "uuid"])
        assert args.parse_imu is False

    def test_no_ui_off_by_default(self):
        args = parse_args([])
        assert args.no_ui is False

    def test_no_ui_flag(self):
        args = parse_args(["--no-ui"])
        assert args.no_ui is True

    def test_parse_imu_flag(self):
        args = parse_args(["--name", "test", "--parse-imu", "uuid"])
        assert args.parse_imu is True

    def test_defaults_unchanged_when_no_new_flags(self):
        """All new flags should have sensible defaults that don't change behavior."""
        args = parse_args(["--name", "test", "uuid"])
        assert args.map_click is None
        assert args.map_vol_plus is None
        assert args.map_vol_minus is None
        assert args.map_app is None
        assert args.map_home is None
        assert args.acceleration is False
        assert args.parse_imu is False
