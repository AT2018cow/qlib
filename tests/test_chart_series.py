from chart_series import align_bin_values_to_calendar_tail


def test_aligns_bin_start_index_to_calendar_tail():
    # Calendar indices 0..9, series starts at 3 and runs through 9.
    out = align_bin_values_to_calendar_tail(
        start_index=3,
        values=[30, 31, 32, 33, 34, 35, 36],
        calendar_length=10,
        window=5,
    )
    assert out == [32.0, 33.0, 34.0, 35.0, 36.0]


def test_preserves_missing_prefix_for_recent_listing():
    out = align_bin_values_to_calendar_tail(
        start_index=8,
        values=[80, 81],
        calendar_length=10,
        window=5,
    )
    assert out == [None, None, None, 80.0, 81.0]


def test_preserves_nan_as_gap():
    out = align_bin_values_to_calendar_tail(
        start_index=0,
        values=[1.0, float("nan"), 3.0],
        calendar_length=3,
        window=3,
    )
    assert out == [1.0, None, 3.0]


def test_preserves_missing_suffix_when_series_ends_before_calendar():
    out = align_bin_values_to_calendar_tail(
        start_index=0,
        values=[1, 2, 3],
        calendar_length=5,
        window=5,
    )
    assert out == [1.0, 2.0, 3.0, None, None]


def test_does_not_emit_invalid_json_infinity():
    out = align_bin_values_to_calendar_tail(
        start_index=0,
        values=[1.0, float("inf"), float("-inf"), 4.0],
        calendar_length=4,
        window=4,
    )
    assert out == [1.0, None, None, 4.0]
