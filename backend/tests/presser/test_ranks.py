from fractions import Fraction

from app.presser.facts.ranks import is_notable, move_ratio, thresholds_entered, thresholds_left


def test_ten_thousand_is_inside_the_top_10k_and_ten_thousand_one_is_not():
    assert thresholds_entered(10_000, 10_001, False) == [10_000]
    assert thresholds_entered(10_001, 20_000, False) == []
    assert thresholds_left(10_001, 10_000) == [10_000]
    assert thresholds_left(10_000, 9_000) == []


def test_entering_and_leaving_several_thresholds_in_threshold_order():
    assert thresholds_entered(5_000, 2_000_000, False) == [1_000_000, 100_000, 10_000]
    assert thresholds_left(2_000_000, 5_000) == [1_000_000, 100_000, 10_000]


def test_rise_inside_the_top_10k_is_notable():
    assert is_notable(7_990, 8_000, False)
    assert not is_notable(8_000, 7_990, False)


def test_halving_is_notable_without_a_threshold():
    assert is_notable(2_000_000, 4_000_000, False)
    assert not is_notable(2_000_001, 4_000_000, False)
    assert is_notable(200_000, 400_000, False)
    assert is_notable(15_000, 30_000, False)


def test_spec_pair_across_the_one_million_threshold():
    assert is_notable(1_000_000, 2_000_000, False)
    assert not is_notable(1_000_001, 2_000_000, False)


def test_doubling_is_notable_for_a_fall():
    assert is_notable(4_000_000, 2_000_000, False)
    assert not is_notable(3_999_999, 2_000_000, False)


def test_small_move_around_one_million_is_not_news():
    assert not is_notable(1_000_001, 1_100_000, False)


def test_first_gameweek_counts_thresholds_as_entered():
    assert thresholds_entered(9_000, None, True) == [1_000_000, 100_000, 10_000]
    assert thresholds_entered(2_000_000, None, True) == []
    assert is_notable(9_000, None, True)
    assert not is_notable(2_000_000, None, True)


def test_missing_previous_rank_after_the_first_gameweek_is_no_movement():
    assert thresholds_entered(9_000, None, False) == []
    assert thresholds_left(9_000, None) == []
    assert not is_notable(9_000, None, False)


def test_move_ratio_is_exact_and_ranks_by_ratio_not_places():
    assert move_ratio(100_000, 300_000) > move_ratio(1_400_000, 3_000_000)
    assert move_ratio(1_400_000, 3_000_000) == Fraction(15, 7)
    assert move_ratio(1_900_000, 900_000) == Fraction(19, 9)
