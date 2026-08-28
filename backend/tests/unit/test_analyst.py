"""Unit tests for deterministic mathematical functions used by the Analyst agent."""

from __future__ import annotations

import pytest

from app.workflow.nodes.analyst import (
    calculate_average,
    calculate_cagr,
    calculate_min_max,
    calculate_percentage_change,
)


def test_calculate_percentage_change_standard() -> None:
    """Validate positive, negative, and zero percentage shifts."""
    assert calculate_percentage_change(old_value=100.0, new_value=125.0) == 25.0
    assert calculate_percentage_change(old_value=100.0, new_value=75.0) == -25.0
    assert calculate_percentage_change(old_value=50.0, new_value=50.0) == 0.0


def test_calculate_percentage_change_zero_division() -> None:
    """Assert ValueError when dividing by zero base value."""
    with pytest.raises(ValueError, match="Base value cannot be zero"):
        calculate_percentage_change(old_value=0.0, new_value=10.0)


def test_calculate_average_metrics() -> None:
    """Validate arithmetic mean calculation and empty input guards."""
    assert calculate_average([10.0, 20.0, 30.0, 40.0]) == 25.0
    assert calculate_average([5.5]) == 5.5
    assert calculate_average([]) == 0.0


def test_calculate_min_max_spread() -> None:
    """Validate min, max, and spread calculations across series."""
    values = [12.5, 3.2, 45.0, 8.1]
    result = calculate_min_max(values)
    assert result == {"min": 3.2, "max": 45.0, "spread": 41.8}

    empty_result = calculate_min_max([])
    assert empty_result == {"min": 0.0, "max": 0.0, "spread": 0.0}


def test_calculate_cagr() -> None:
    """Validate compound annual growth rate calculation."""
    # 100 growing to 200 over 3 years: ((200/100)**(1/3) - 1) * 100 = 25.9921...
    cagr = calculate_cagr(start_value=100.0, end_value=200.0, periods=3)
    assert round(cagr, 2) == 25.99

    with pytest.raises(ValueError, match="Periods must be greater than zero"):
        calculate_cagr(start_value=100.0, end_value=200.0, periods=0)