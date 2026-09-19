"""Small synthetic numerical-interface fixture for harness qualification."""
import math


def interpolate(points, queries):
    """Linear interpolation between (time, value) pairs.

    Args:
        points: List of (time, value) tuples with strictly increasing finite times.
        queries: List of query times (finite or within 1e-12 of domain endpoints).

    Returns:
        List of interpolated values in query order. Queries beyond domain return None.
        Values at domain endpoints are included.

    Raises:
        ValueError: If points has fewer than 2 points, duplicate/decreasing times,
                    or non-finite values.
    """
    # Validate at least 2 points
    if len(points) < 2:
        raise ValueError("At least two points are required")

    # Validate points and extract times/values
    times = []
    values = []
    for i, (t, v) in enumerate(points):
        # Check finite values
        if not math.isfinite(t) or not math.isfinite(v):
            raise ValueError("All time and value must be finite")
        # Check strictly increasing times
        if i > 0 and t <= times[-1]:
            raise ValueError("Times must be strictly increasing with no duplicates")
        times.append(t)
        values.append(v)

    # Domain bounds
    t_min = times[0]
    t_max = times[-1]

    # Tolerance for endpoint handling
    tol = 1e-12

    results = []
    for q in queries:
        # Check finite query - non-finite queries raise ValueError
        if not math.isfinite(q):
            raise ValueError("Query must be finite")

        # Handle queries within tolerance of endpoints
        if q <= t_min:
            if q >= t_min - tol:
                # Within tolerance of minimum - return first value
                results.append(values[0])
            else:
                # Beyond minimum by more than tolerance
                results.append(None)
            continue

        if q >= t_max:
            if q <= t_max + tol:
                # Within tolerance of maximum - return last value
                results.append(values[-1])
            else:
                # Beyond maximum by more than tolerance
                results.append(None)
            continue

        # Find the interval containing the query
        # Binary search would be more efficient but linear is sufficient
        for i in range(len(times) - 1):
            if times[i] <= q <= times[i + 1]:
                # Linear interpolation
                t0, t1 = times[i], times[i + 1]
                v0, v1 = values[i], values[i + 1]
                # t = t0 + (t1 - t0) * alpha
                # v = v0 + (v1 - v0) * alpha
                alpha = (q - t0) / (t1 - t0)
                v_interp = v0 + (v1 - v0) * alpha
                results.append(v_interp)
                break

    return results
