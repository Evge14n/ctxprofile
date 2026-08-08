from __future__ import annotations

from ctxprofile.apportion import apportion


def test_parts_sum_to_the_total_exactly():
    parts = apportion([3, 3, 3], 10)
    assert sum(parts) == 10
    assert sorted(parts) == [3, 3, 4]


def test_leftover_goes_to_the_largest_remainders():
    assert apportion([1, 1, 8], 11) == [1, 1, 9]


def test_zero_weights_split_evenly_rather_than_dropping_the_total():
    parts = apportion([0, 0, 0], 7)
    assert sum(parts) == 7
    assert parts == [3, 2, 2]


def test_empty_and_non_positive_totals():
    assert apportion([], 100) == []
    assert apportion([1, 2], 0) == [0, 0]
    assert apportion([1, 2], -5) == [0, 0]


def test_no_part_is_negative_for_any_weight_mix():
    parts = apportion([100, 1, 1], 3)
    assert min(parts) >= 0
    assert sum(parts) == 3
