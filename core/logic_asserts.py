def assert_ohlc_logic(open_price: float, high_price: float, low_price: float, close_price: float):
    """
    Financial Logic: Validate OHLC relationships.
    High should be >= Low, Open, Close. Low should be <= High, Open, Close.
    """
    assert high_price >= low_price, f"High ({high_price}) must be >= Low ({low_price})"
    assert high_price >= open_price, f"High ({high_price}) must be >= Open ({open_price})"
    assert high_price >= close_price, f"High ({high_price}) must be >= Close ({close_price})"
    assert low_price <= open_price, f"Low ({low_price}) must be <= Open ({open_price})"
    assert low_price <= close_price, f"Low ({low_price}) must be <= Close ({close_price})"

def assert_time_series_continuity(timestamps: list[int], interval_ms: int):
    """
    Financial Logic: Validate continuous time series interval.
    """
    for i in range(1, len(timestamps)):
        diff = timestamps[i] - timestamps[i-1]
        assert diff == interval_ms, f"Discontinuity detected: diff {diff} != {interval_ms} at index {i}"
