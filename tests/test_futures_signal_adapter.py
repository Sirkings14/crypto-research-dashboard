import pandas as pd
import pytest

from futures_signal_adapter import make_futures_target_sides


def frame(closes):
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(closes), freq="h", tz="UTC"),
        "close": closes,
    })


def test_signal_is_shifted_to_next_candle_open():
    # SMA 2/3 is false until the third close; that completed signal is
    # actionable only at the fourth candle's open.
    result = make_futures_target_sides(
        frame([3, 2, 1, 4, 5, 6]),
        {"family": "sma", "fast": 2, "slow": 3},
    )
    assert result == ["flat", "flat", "flat", "flat", "long", "long"]


def test_first_candle_is_flat_even_if_strategy_signal_is_true():
    result = make_futures_target_sides(
        frame([1, 2, 3, 4]),
        {"family": "momentum", "window": 1, "threshold": 0.1},
    )
    assert result[0] == "flat"
    assert result[1] == "flat"
    assert result[2] == "long"


@pytest.mark.parametrize("timestamps", [
    ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
    ["2026-01-01T01:00:00Z", "2026-01-01T00:00:00Z"],
])
def test_rejects_duplicate_or_unordered_timestamps(timestamps):
    data = pd.DataFrame({"timestamp": timestamps, "close": [1, 2]})
    with pytest.raises(ValueError, match="unique and strictly increasing"):
        make_futures_target_sides(data, {"family": "momentum", "window": 1, "threshold": 0.1})


def test_rejects_invalid_prices_and_missing_columns():
    with pytest.raises(ValueError, match="finite and positive"):
        make_futures_target_sides(frame([1, 0, 2]), {"family": "momentum", "window": 1, "threshold": 0.1})
    with pytest.raises(ValueError, match="missing required columns"):
        make_futures_target_sides(pd.DataFrame({"close": [1, 2]}), {"family": "momentum", "window": 1, "threshold": 0.1})


def test_empty_input_rejected():
    with pytest.raises(ValueError, match="at least one candle"):
        make_futures_target_sides(frame([]), {"family": "momentum", "window": 1, "threshold": 0.1})
