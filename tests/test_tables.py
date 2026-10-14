from __future__ import annotations

from app.converters.tables import (
    _clean_cell,
    _iou,
    _normalize,
    _valid,
    table_to_markdown,
)


class TestCleanCell:
    def test_none_returns_empty(self):
        assert _clean_cell(None) == ""

    def test_string_strips_whitespace(self):
        assert _clean_cell("  hello  world  ") == "hello world"

    def test_newlines_replaced_with_space(self):
        assert _clean_cell("hello\nworld") == "hello world"

    def test_multiple_newlines_collapsed(self):
        assert _clean_cell("hello\n\n\nworld") == "hello world"

    def test_tabs_and_spaces_normalized(self):
        assert _clean_cell("\thello\tworld\t") == "hello world"

    def test_empty_string_returns_empty(self):
        assert _clean_cell("") == ""

    def test_numbers_converted_to_string(self):
        assert _clean_cell(123) == "123"
        assert _clean_cell(45.67) == "45.67"


class TestNormalize:
    def test_empty_rows_returns_empty(self):
        assert _normalize([]) == []

    def test_none_rows_returns_empty(self):
        assert _normalize(None) == []

    def test_normalizes_each_cell(self):
        rows = [["  a  ", "b\nc"], ["d", None]]
        result = _normalize(rows)
        assert result == [["a", "b c"], ["d", ""]]

    def test_preserves_row_structure(self):
        rows = [["a", "b"], ["c", "d"]]
        result = _normalize(rows)
        assert result == [["a", "b"], ["c", "d"]]


class TestValid:
    def test_single_row_invalid(self):
        assert _valid([["a", "b"]], None) is False

    def test_single_column_invalid(self):
        assert _valid([["a"], ["b"]], None) is False

    def test_valid_table(self):
        assert _valid([["a", "b"], ["c", "d"]], None) is True

    def test_cell_too_long_invalid(self):
        long_cell = "x" * 301
        assert _valid([[long_cell, "b"], ["c", "d"]], None) is False

    def test_cell_at_limit_valid(self):
        at_limit = "x" * 300
        assert _valid([[at_limit, "b"], ["c", "d"]], None) is True


class TestIOU:
    def test_no_overlap_returns_zero(self):
        a = (0, 0, 10, 10)
        b = (20, 20, 30, 30)
        assert _iou(a, b) == 0.0

    def test_partial_overlap(self):
        a = (0, 0, 10, 10)
        b = (5, 5, 15, 15)
        result = _iou(a, b)
        assert 0 < result < 1
        assert abs(result - 0.142857) < 0.001

    def test_full_overlap_returns_one(self):
        a = (0, 0, 10, 10)
        b = (0, 0, 10, 10)
        assert _iou(a, b) == 1.0

    def test_one_inside_another(self):
        a = (0, 0, 10, 10)
        b = (2, 2, 8, 8)
        result = _iou(a, b)
        assert abs(result - 0.36) < 0.001

    def test_edge_touch_no_overlap(self):
        a = (0, 0, 10, 10)
        b = (10, 10, 20, 20)
        assert _iou(a, b) == 0.0


class TestTableToMarkdown:
    def test_empty_rows_returns_empty(self):
        assert table_to_markdown([]) == ""

    def test_single_row(self):
        result = table_to_markdown([["A", "B"]])
        assert result == "| A | B |\n| --- | --- |"

    def test_multiple_rows(self):
        result = table_to_markdown([["A", "B"], ["1", "2"], ["3", "4"]])
        expected = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
        assert result == expected

    def test_uneven_columns_padded(self):
        result = table_to_markdown([["A", "B", "C"], ["1", "2"]])
        expected = "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 |  |"
        assert result == expected

    def test_special_chars_escaped(self):
        result = table_to_markdown([["A|B", "C"], ["D", "E|F"]])
        assert "| A|B | C |" in result
        assert "| D | E|F |" in result