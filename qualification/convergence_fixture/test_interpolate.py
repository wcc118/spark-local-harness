"""Tests for convergence_fixture.interpolate."""
import math

from interpolate import interpolate


def test_midpoint():
    """Basic linear interpolation at midpoint."""
    assert interpolate([(0.0, 0.0), (1.0, 10.0)], [0.5]) == [5.0]


def test_endpoint_values():
    """Values at domain endpoints are returned correctly."""
    points = [(0.0, 0.0), (2.0, 20.0)]
    result = interpolate(points, [0.0, 2.0])
    assert result == [0.0, 20.0]


def test_multiple_queries():
    """Multiple queries in order return corresponding values."""
    points = [(0.0, 0.0), (1.0, 10.0), (2.0, 20.0)]
    result = interpolate(points, [0.5, 1.5])
    assert result == [5.0, 15.0]


def test_query_before_domain():
    """Query before domain returns None."""
    points = [(1.0, 10.0), (2.0, 20.0)]
    result = interpolate(points, [0.5])
    assert result == [None]


def test_query_after_domain():
    """Query after domain returns None."""
    points = [(1.0, 10.0), (2.0, 20.0)]
    result = interpolate(points, [2.5])
    assert result == [None]


def test_endpoint_tolerance_below():
    """Query within 1e-12 of minimum endpoint returns that value."""
    points = [(1.0, 10.0), (2.0, 20.0)]
    result = interpolate(points, [1.0 - 1e-12])
    assert result == [10.0]


def test_endpoint_tolerance_above():
    """Query within 1e-12 of maximum endpoint returns that value."""
    points = [(1.0, 10.0), (2.0, 20.0)]
    result = interpolate(points, [2.0 + 1e-12])
    assert result == [20.0]


def test_beyond_tolerance_below():
    """Query beyond 1e-12 of minimum returns None."""
    points = [(1.0, 10.0), (2.0, 20.0)]
    result = interpolate(points, [1.0 - 1e-11])
    assert result == [None]


def test_beyond_tolerance_above():
    """Query beyond 1e-12 of maximum returns None."""
    points = [(1.0, 10.0), (2.0, 20.0)]
    result = interpolate(points, [2.0 + 1e-11])
    assert result == [None]


def test_fewer_than_two_points():
    """Fewer than two points raises ValueError."""
    try:
        interpolate([(0.0, 0.0)], [0.5])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_empty_points():
    """Empty points raises ValueError."""
    try:
        interpolate([], [0.5])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_duplicate_times():
    """Duplicate times raises ValueError."""
    try:
        interpolate([(0.0, 0.0), (0.0, 10.0)], [0.5])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_decreasing_times():
    """Decreasing times raises ValueError."""
    try:
        interpolate([(1.0, 10.0), (0.0, 0.0)], [0.5])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_non_finite_time():
    """Non-finite time raises ValueError."""
    try:
        interpolate([(float('inf'), 0.0), (1.0, 10.0)], [0.5])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_non_finite_value():
    """Non-finite value raises ValueError."""
    try:
        interpolate([(0.0, float('inf')), (1.0, 10.0)], [0.5])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_negative_inf_value():
    """Negative infinite value raises ValueError."""
    try:
        interpolate([(0.0, float('-inf')), (1.0, 10.0)], [0.5])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_nan_value():
    """NaN value raises ValueError."""
    try:
        interpolate([(0.0, float('nan')), (1.0, 10.0)], [0.5])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_non_finite_query_positive_inf():
    """Positive infinite query raises ValueError."""
    points = [(0.0, 0.0), (1.0, 10.0)]
    try:
        interpolate(points, [float('inf')])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_non_finite_query_negative_inf():
    """Negative infinite query raises ValueError."""
    points = [(0.0, 0.0), (1.0, 10.0)]
    try:
        interpolate(points, [float('-inf')])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_non_finite_query_nan():
    """NaN query raises ValueError."""
    points = [(0.0, 0.0), (1.0, 10.0)]
    try:
        interpolate(points, [float('nan')])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_negative_slope():
    """Negative slope interpolation works correctly."""
    points = [(0.0, 20.0), (1.0, 10.0)]
    result = interpolate(points, [0.5])
    assert result == [15.0]


def test_zero_value_range():
    """Zero value range (constant) interpolation works."""
    points = [(0.0, 5.0), (1.0, 5.0)]
    result = interpolate(points, [0.0, 0.5, 1.0])
    assert result == [5.0, 5.0, 5.0]


def test_non_mutation_points():
    """Original points are not mutated."""
    points = [(0.0, 0.0), (1.0, 10.0)]
    points_copy = list(points)
    interpolate(points, [0.5])
    assert points == points_copy


def test_non_mutation_queries():
    """Original queries are not mutated."""
    queries = [0.5]
    queries_copy = list(queries)
    interpolate([(0.0, 0.0), (1.0, 10.0)], queries)
    assert queries == queries_copy


def test_multiple_queries_mixed_valid_invalid():
    """Mixed valid and invalid queries return appropriate results."""
    points = [(0.0, 0.0), (2.0, 20.0)]
    result = interpolate(points, [1.0, -1.0, 3.0])
    assert result == [10.0, None, None]


def test_single_interval_multiple_queries():
    """Multiple queries within single interval."""
    points = [(0.0, 0.0), (1.0, 1.0)]
    result = interpolate(points, [0.25, 0.5, 0.75])
    assert result == [0.25, 0.5, 0.75]


def test_negative_time_values():
    """Negative time and value handling."""
    points = [(-1.0, -10.0), (1.0, 10.0)]
    result = interpolate(points, [0.0])
    assert result == [0.0]


def test_very_small_interval():
    """Very small time interval."""
    points = [(0.0, 0.0), (1e-15, 1e-15)]
    result = interpolate(points, [5e-16])
    assert len(result) == 1
    assert math.isfinite(result[0])
