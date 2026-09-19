from interpolate import interpolate


def test_midpoint():
    assert interpolate([(0.0, 0.0), (1.0, 10.0)], [0.5]) == [5.0]
