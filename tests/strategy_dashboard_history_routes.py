"""Existing fixed scenario IDs bound to the real raw-history engine."""

HISTORY_SOURCE_IDS = frozenset(
    [f"FM-DRAWDOWN-{index:03d}" for index in (1, 2, 3, 4, 5, 8)]
    + [f"FXT-DRAWDOWN-{index:03d}" for index in (1, 2, 3)]
    + [f"FM-VOL-{index:03d}" for index in range(1, 7)]
    + [f"FXT-VOL-{index:03d}" for index in (1, 2)]
    + [f"FM-SHARPE-{index:03d}" for index in range(1, 6)]
    + [f"FM-ANNUAL-{index:03d}" for index in range(1, 8)]
    + [f"FXT-ANNUAL-{index:03d}" for index in (1, 2)]
    + [f"FM-CALMAR-{index:03d}" for index in (1, 2, 3)]
    + [f"FXT-CALMAR-{index:03d}" for index in (1, 2, 3)]
    + ["FXT-RETURN-001", "FXT-RETURN-002"]
)
