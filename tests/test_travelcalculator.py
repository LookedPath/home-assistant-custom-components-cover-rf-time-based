"""Regressions for timing, rounding and direction changes."""

from unittest.mock import patch

import pytest

from custom_components.cover_rf_time_based.travelcalculator import TravelCalculator, TravelStatus


@pytest.mark.parametrize(
    "start,target,down,up", [(100, 50, 25, 25), (0, 50, 25, 25), (100, 0, 40, 20), (0, 100, 40, 20)]
)
def test_completion_uses_elapsed_time(start, target, down, up):
    tc = TravelCalculator(down, up)
    tc.time_set_from_outside = 100
    tc.set_position(start)
    tc.start_travel(target)
    duration = abs(target - start) / 100 * (up if target > start else down)
    tc.time_set_from_outside = 100 + duration - 0.01
    assert tc.is_traveling()
    assert not tc.position_reached()
    tc.time_set_from_outside = 100 + duration
    assert tc.position_reached()
    assert not tc.is_traveling()
    assert tc.current_position() == target


def test_display_rounding_does_not_finish_closing():
    tc = TravelCalculator(25, 25)
    tc.time_set_from_outside = 100
    tc.set_position(100)
    tc.start_travel(50)
    tc.time_set_from_outside = 112.3
    assert tc.current_position() == 50
    assert not tc.position_reached()


def test_closed_state_waits_for_full_duration():
    tc = TravelCalculator(25, 25)
    tc.set_position(100)
    tc.time_set_from_outside = 100
    tc.start_travel_down()
    tc.time_set_from_outside = 124.9
    assert tc.current_position() == 0
    assert not tc.is_closed()
    tc.time_set_from_outside = 125
    assert tc.is_closed()


def test_stop_and_reversal_preserve_fractional_progress():
    tc = TravelCalculator(40, 20)
    tc.time_set_from_outside = 100
    tc.start_travel_up()
    tc.time_set_from_outside = 102.1
    tc.start_travel_down()
    assert tc.last_known_position == pytest.approx(10.5)
    tc.time_set_from_outside = 104.1
    tc.stop()
    assert tc.last_known_position == pytest.approx(5.5)
    tc.time_set_from_outside = 200
    assert tc.current_position() == 5
    assert not tc.is_traveling()


def test_monotonic_clock_ignores_wall_clock_adjustments():
    tc = TravelCalculator(25, 25)
    with patch("time.monotonic", return_value=100), patch("time.time", return_value=1000):
        tc.start_travel_up()
    with patch("time.monotonic", return_value=105), patch("time.time", return_value=1):
        assert tc.current_position() == 20


@pytest.mark.parametrize("now,expected", [(99, 0), (1000, 100)])
def test_position_is_bounded(now, expected):
    tc = TravelCalculator(25, 25)
    tc.time_set_from_outside = 100
    tc.start_travel_up()
    tc.time_set_from_outside = now
    assert tc.current_position() == expected


def test_same_position_and_sensor_correction_stop_travel():
    tc = TravelCalculator(25, 25)
    tc.start_travel(0)
    assert tc.position_reached()
    assert tc.travel_direction == TravelStatus.STOPPED
    tc.start_travel_up()
    tc.set_position(50)
    assert not tc.is_traveling()
    assert tc.travel_direction == TravelStatus.STOPPED


@pytest.mark.parametrize("position", [-1, 101])
def test_invalid_positions_are_rejected(position):
    tc = TravelCalculator(25, 25)
    with pytest.raises(ValueError):
        tc.set_position(position)
    with pytest.raises(ValueError):
        tc.start_travel(position)


@pytest.mark.parametrize("down,up", [(0, 25), (25, 0), (-1, 25)])
def test_zero_and_negative_durations_are_rejected(down, up):
    with pytest.raises(ValueError):
        TravelCalculator(down, up)
