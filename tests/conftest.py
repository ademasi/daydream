"""Shared test fixtures for daydream-remote tests.

Patches sys.modules["uinput"] with a MagicMock before any daydream import,
since the real uinput module requires /dev/uinput at import time.
"""

import sys
from unittest.mock import MagicMock

# --- Build a fake uinput module with realistic event constants ---
_mock_uinput = MagicMock()

# uinput events are (type, code) tuples in the real library
_mock_uinput.BTN_LEFT = (0x01, 0x110)
_mock_uinput.KEY_LEFT = (0x01, 0x69)
_mock_uinput.KEY_RIGHT = (0x01, 0x6A)
_mock_uinput.KEY_ESC = (0x01, 0x01)
_mock_uinput.KEY_LEFTMETA = (0x01, 0x7D)
_mock_uinput.KEY_SPACE = (0x01, 0x39)
_mock_uinput.KEY_ENTER = (0x01, 0x1C)
_mock_uinput.KEY_UP = (0x01, 0x67)
_mock_uinput.KEY_DOWN = (0x01, 0x6C)
_mock_uinput.KEY_F5 = (0x01, 0x3F)
_mock_uinput.KEY_F11 = (0x01, 0x57)
_mock_uinput.REL_X = (0x02, 0x00)
_mock_uinput.REL_Y = (0x02, 0x01)

# Patch before daydream can be imported
sys.modules["uinput"] = _mock_uinput

import pytest  # noqa: E402

import daydream  # noqa: E402


@pytest.fixture
def mock_uinput_device():
    """Returns a fresh MagicMock that acts as a uinput.Device."""
    device = MagicMock()
    device.emit = MagicMock()
    device.emit_click = MagicMock()
    device.syn = MagicMock()
    device.destroy = MagicMock()
    return device


@pytest.fixture
def bridge(mock_uinput_device):
    """Returns a DaydreamBridge with a mock uinput device."""
    b = daydream.DaydreamBridge()
    b.uinput_device = mock_uinput_device
    return b
