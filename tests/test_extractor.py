from __future__ import annotations

from unittest.mock import Mock, patch

import pymupdf
import pytest

from app.converters.extractor import _fallback_text, extract_text


class TestFallbackText:
    def test_simple_text(self):
        page = Mock()
        page.get_text.return_value = "Hello\nWorld\n\nTest"
        result = _fallback_text(page)
        assert result == "Hello\nWorld\nTest"

    def test_collapses_whitespace(self):
        page = Mock()
        page.get_text.return_value = "  Hello   World  "
        result = _fallback_text(page)
        assert result == "Hello World"

    def test_removes_empty_lines(self):
        page = Mock()
        page.get_text.return_value = "Line 1\n\n\nLine 2\n"
        result = _fallback_text(page)
        assert result == "Line 1\nLine 2"


class TestExtractText:
    def test_no_words_uses_fallback(self):
        page = Mock()
        page.get_text.return_value = []
        with patch("app.converters.extractor._fallback_text") as mock_fallback:
            mock_fallback.return_value = "fallback text"
            result = extract_text(page)
            assert result == "fallback text"
            mock_fallback.assert_called_once_with(page)

    def test_empty_words_returns_empty(self):
        page = Mock()
        page.get_text.return_value = []
        with patch("app.converters.extractor._fallback_text") as mock_fallback:
            mock_fallback.return_value = ""
            result = extract_text(page)
            assert result == ""

    def test_excludes_words_in_rect(self):
        page = Mock()
        word1 = (100, 100, 120, 110, "Hello", 0, 0, 0)
        word2 = (200, 200, 220, 210, "World", 0, 0, 0)
        page.get_text.return_value = [word1, word2]

        exclude = [pymupdf.Rect(90, 90, 130, 120)]
        result = extract_text(page, exclude)

        assert "Hello" not in result
        assert "World" in result

    def test_excludes_multiple_rects(self):
        page = Mock()
        words = [
            (100, 100, 120, 110, "A", 0, 0, 0),
            (200, 200, 220, 210, "B", 0, 0, 0),
            (300, 300, 320, 310, "C", 0, 0, 0),
        ]
        page.get_text.return_value = words

        exclude = [
            pymupdf.Rect(90, 90, 130, 120),
            pymupdf.Rect(290, 290, 330, 320),
        ]
        result = extract_text(page, exclude)

        assert "A" not in result
        assert "C" not in result
        assert "B" in result

    def test_groups_words_by_rounded_y(self):
        page = Mock()
        words = [
            (100, 99, 120, 109, "Word1", 0, 0, 0),
            (150, 100, 170, 110, "Word2", 0, 0, 0),
            (200, 200, 220, 210, "Word3", 0, 0, 0),
        ]
        page.get_text.return_value = words

        result = extract_text(page)
        lines = result.split("\n")
        assert len(lines) == 2
        assert "Word1 Word2" in lines[0]
        assert "Word3" in lines[1]

    def test_sorts_words_by_x_within_line(self):
        page = Mock()
        words = [
            (200, 100, 220, 110, "Second", 0, 0, 0),
            (100, 100, 120, 110, "First", 0, 0, 0),
        ]
        page.get_text.return_value = words

        result = extract_text(page)
        assert result.index("First") < result.index("Second")

    def test_handles_empty_exclude_list(self):
        page = Mock()
        page.get_text.return_value = [(100, 100, 120, 110, "Hello", 0, 0, 0)]

        result = extract_text(page, [])
        assert "Hello" in result

    def test_handles_none_exclude(self):
        page = Mock()
        page.get_text.return_value = [(100, 100, 120, 110, "Hello", 0, 0, 0)]

        result = extract_text(page, None)
        assert "Hello" in result

    def test_complex_layout(self):
        page = Mock()
        words = [
            (100, 100, 150, 110, "Header", 0, 0, 0),
            (100, 150, 150, 160, "Column1", 0, 0, 0),
            (200, 150, 250, 160, "Column2", 0, 0, 0),
            (100, 200, 150, 210, "Footer", 0, 0, 0),
        ]
        page.get_text.return_value = words

        result = extract_text(page)
        lines = result.split("\n")
        assert len(lines) == 3
        assert "Header" in lines[0]
        assert "Column1 Column2" in lines[1]
        assert "Footer" in lines[2]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])