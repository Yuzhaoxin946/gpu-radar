import pytest

from app.classification import MAX_HISTORY_POINTS, next_history


def test_irregular_cadence_is_time_weighted_and_boundary_anchor_is_clipped():
    history = []
    # 90% during the first 20 seconds, then 0%; every receipt gap is <=30s.
    for instant, utilization in [(0, 90), (20, 0)] + [(instant, 0) for instant in range(50, 291, 30)] + [(300, 0)]:
        history, average, coverage = next_history(history, instant, utilization, 300)
    assert coverage == 300
    assert average == pytest.approx(6.0)
    history, average, coverage = next_history(history, 315, 0, 300)
    assert history[0][0] == 0  # Anchor precedes the new cutoff at t=15.
    assert average == pytest.approx(1.5)  # Only [15,20] of the 90% interval remains.
    history, average, coverage = next_history(history, 330, 0, 300)
    assert history[0][0] == 20  # Earlier, unnecessary anchors are pruned.
    assert average == 0


def test_history_is_bounded_without_fabricating_missing_coverage():
    history = []
    for index in range(MAX_HISTORY_POINTS + 200):
        history, average, coverage = next_history(history, index / 10, 0, 300)
    assert len(history) == MAX_HISTORY_POINTS
    assert average is None
    assert coverage == pytest.approx((MAX_HISTORY_POINTS - 1) / 10)


def test_equal_receipt_timestamp_never_advances_window():
    history, average, coverage = next_history([], 100, 0, 300)
    for _ in range(10):
        history, average, coverage = next_history(history, 100, 50, 300)
    assert len(history) == 1
    assert average is None and coverage == 0
