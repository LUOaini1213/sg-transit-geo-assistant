"""The execution-accuracy comparison used by eval/run_eval.py."""
from compare import results_match


def test_same_rows_any_order():
    assert results_match([["a"], ["b"]], [["b"], ["a"]])


def test_extra_predicted_columns_allowed():
    assert results_match([["01012"]], [["01012", "Hotel Grand Pacific", 1110.8]])


def test_column_order_ignored():
    assert results_match([["A", 1]], [[1, "A"]])


def test_missing_gold_column_fails():
    assert not results_match([["01012", 1110.8]], [["01012"]])


def test_row_count_must_match():
    assert not results_match([["a"]], [["a"], ["b"]])
    assert not results_match([["a"], ["b"]], [["a"]])


def test_numbers_at_four_significant_figures():
    assert results_match([[1110.8]], [[1110.8000001]])
    assert results_match([[286]], [[286.0]])
    assert not results_match([[1110.8]], [[1120.0]])


def test_strings_exact():
    assert not results_match([["ORCHARD RD"]], [["Orchard Rd"]])


def test_rows_must_pair_up_not_just_columns():
    gold = [["a", 1], ["b", 2]]
    assert results_match(gold, [["b", 2], ["a", 1]])
    assert not results_match(gold, [["a", 2], ["b", 1]])
