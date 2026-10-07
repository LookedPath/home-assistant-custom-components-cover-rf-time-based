"""
Module TravelCalculator provides functionality for predicting the current position of a Cover.

E.g.:

* Given a Cover that takes 100 seconds to travel from top to bottom.
* Starting from position 90, directed to position 60 at time 0.
* At time 10 TravelCalculator will return position 80 (final position not reached).
* At time 20 TravelCalculator will return position 70 (final position not reached).
* At time 30 TravelCalculator will return position 60 (final position reached).

Borrowed from XKNX implementation: https://github.com/XKNX/xknx/blob/main/xknx/devices/travelcalculator.py release 0.9.4.
Thanks @farmio https://github.com/nagyrobi/home-assistant-custom-components-cover-rf-time-based/issues/50#issuecomment-1212172751
"""

import time
from enum import Enum


class PositionType(Enum):
    """Enum class for different type of calculated positions."""

    UNKNOWN = 1
    CALCULATED = 2
    CONFIRMED = 3


class TravelStatus(Enum):
    """Enum class for travel status."""

    DIRECTION_UP = 1
    DIRECTION_DOWN = 2
    STOPPED = 3


class TravelCalculator:
    """Predict position using elapsed monotonic time, retaining sub-percent precision."""

    def __init__(self, travel_time_down, travel_time_up):
        if travel_time_down <= 0 or travel_time_up <= 0:
            raise ValueError("Travel times must be greater than zero")
        self.position_type = PositionType.UNKNOWN
        self.last_known_position = 0
        self.travel_time_down = travel_time_down
        self.travel_time_up = travel_time_up
        self.travel_to_position = 0
        self.travel_started_time = 0
        self.travel_direction = TravelStatus.STOPPED
        self.position_closed = 0
        self.position_open = 100
        self.time_set_from_outside = None

    @staticmethod
    def _validate_position(position):
        if not 0 <= position <= 100:
            raise ValueError("Position must be between 0 and 100")
        return position

    def set_position(self, position):
        """Set a known position and stop the previous calculation."""
        self.last_known_position = self._validate_position(position)
        self.travel_to_position = position
        self.position_type = PositionType.CONFIRMED
        self.travel_direction = TravelStatus.STOPPED

    def stop(self):
        """Stop traveling without discarding fractional position."""
        self.last_known_position = self._calculate_position()
        self.travel_to_position = self.last_known_position
        self.position_type = PositionType.CALCULATED
        self.travel_direction = TravelStatus.STOPPED

    def start_travel(self, travel_to_position):
        """Start traveling, preserving progress when reversing direction."""
        self._validate_position(travel_to_position)
        self.stop()
        self.travel_started_time = self.current_time()
        self.travel_to_position = travel_to_position
        self.position_type = PositionType.CALCULATED
        if travel_to_position == self.last_known_position:
            self.travel_direction = TravelStatus.STOPPED
        else:
            self.travel_direction = (
                TravelStatus.DIRECTION_UP
                if travel_to_position > self.last_known_position
                else TravelStatus.DIRECTION_DOWN
            )

    def start_travel_up(self):
        """Start traveling up."""
        self.start_travel(self.position_open)

    def start_travel_down(self):
        """Start traveling down."""
        self.start_travel(self.position_closed)

    def current_position(self):
        """Return the integer position used by Home Assistant."""
        return int(self._calculate_position())

    def is_traveling(self):
        """Return whether the travel duration has not yet elapsed."""
        return not self.position_reached()

    def position_reached(self):
        """Determine completion from elapsed time, never from display rounding."""
        if self.travel_direction == TravelStatus.STOPPED:
            return True
        travel_time = self._calculate_travel_time(
            self.travel_to_position - self.last_known_position
        )
        return self.current_time() >= self.travel_started_time + travel_time

    def is_open(self):
        """Return whether the cover has fully opened."""
        return self._calculate_position() == self.position_open

    def is_closed(self):
        """Return whether the cover has fully closed."""
        return self._calculate_position() == self.position_closed

    def _calculate_position(self):
        """Return an unrounded position bounded by the start and target."""
        if self.travel_direction == TravelStatus.STOPPED:
            return self.last_known_position
        relative_position = self.travel_to_position - self.last_known_position
        travel_time = self._calculate_travel_time(relative_position)
        if travel_time == 0:
            return self.travel_to_position
        now = self.current_time()
        if now >= self.travel_started_time + travel_time:
            return self.travel_to_position
        elapsed = max(0, now - self.travel_started_time)
        progress = min(1, elapsed / travel_time)
        return max(0, min(100, self.last_known_position + relative_position * progress))

    def _calculate_travel_time(self, relative_position):
        """Calculate duration using the travel time for the correct direction."""
        travel_time_full = self.travel_time_up if relative_position > 0 else self.travel_time_down
        return travel_time_full * abs(relative_position) / 100

    def current_time(self):
        """Get monotonic time; tests may supply a deterministic clock."""
        if self.time_set_from_outside is not None:
            return self.time_set_from_outside
        return time.monotonic()

    def __eq__(self, other):
        """Compare calculators."""
        if not isinstance(other, TravelCalculator):
            return NotImplemented
        return self.__dict__ == other.__dict__
