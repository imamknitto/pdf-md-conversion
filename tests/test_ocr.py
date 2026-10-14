from __future__ import annotations

import io
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from app.converters.ocr import (
    _box_center,
    _get_engine,
    prepare_image,
    sort_boxes_top_left,
    upscale_safe,
)


class TestBoxCenter:
    def test_normal_box(self):
        box = [[10, 20], [30, 20], [30, 40], [10, 40]]
        cx, cy = _box_center(box)
        assert cx == 20.0
        assert cy == 30.0

    def test_single_point(self):
        box = [[5, 5], [5, 5], [5, 5], [5, 5]]
        cx, cy = _box_center(box)
        assert cx == 5.0
        assert cy == 5.0

    def test_invalid_box_returns_zero(self):
        box = "invalid"
        cx, cy = _box_center(box)
        assert cx == 0.0
        assert cy == 0.0

    def test_empty_box_returns_zero(self):
        box = []
        cx, cy = _box_center(box)
        assert cx == 0.0
        assert cy == 0.0


class TestSortBoxesTopLeft:
    def test_sorts_by_y_then_x(self):
        boxes = [
            {"cx": 100, "cy": 50, "text": "b"},
            {"cx": 50, "cy": 50, "text": "a"},
            {"cx": 50, "cy": 100, "text": "c"},
        ]
        result = sort_boxes_top_left(boxes, y_tol=10)
        assert [b["text"] for b in result] == ["a", "b", "c"]

    def test_groups_by_y_tolerance(self):
        boxes = [
            {"cx": 50, "cy": 50, "text": "a"},
            {"cx": 100, "cy": 55, "text": "b"},
            {"cx": 50, "cy": 100, "text": "c"},
        ]
        result = sort_boxes_top_left(boxes, y_tol=10)
        assert [b["text"] for b in result] == ["a", "b", "c"]

    def test_different_rows_sorted_by_y(self):
        boxes = [
            {"cx": 50, "cy": 100, "text": "c"},
            {"cx": 50, "cy": 50, "text": "a"},
        ]
        result = sort_boxes_top_left(boxes, y_tol=10)
        assert [b["text"] for b in result] == ["a", "c"]

    def test_empty_list_returns_empty(self):
        assert sort_boxes_top_left([]) == []

    def test_single_box_returns_same(self):
        boxes = [{"cx": 50, "cy": 50, "text": "a"}]
        result = sort_boxes_top_left(boxes)
        assert result == boxes


class TestUpscaleSafe:
    def test_no_upscale_when_above_min_side(self):
        img = Image.new("RGB", (1500, 1000))
        result = upscale_safe(img)
        assert result is img

    def test_no_upscale_when_at_min_side(self):
        img = Image.new("RGB", (1200, 800))
        result = upscale_safe(img)
        assert result is img

    def test_upscale_small_image(self):
        img = Image.new("RGB", (600, 400))
        result = upscale_safe(img)
        assert result.width > img.width
        assert result.height > img.height
        assert max(result.width, result.height) <= 1200 * 2

    def test_respects_max_scale(self):
        img = Image.new("RGB", (100, 100))
        result = upscale_safe(img)
        max_allowed = int(100 * 2.0)
        assert result.width <= max_allowed
        assert result.height <= max_allowed

    def test_respects_max_pixels(self):
        img = Image.new("RGB", (2000, 2000))
        result = upscale_safe(img)
        assert result.width * result.height <= 9000000

    def test_invalid_image_returned_as_is(self):
        class BadImage:
            def __getattr__(self, name):
                raise ValueError("bad")

        bad = BadImage()
        result = upscale_safe(bad)
        assert result is bad


class TestPrepareImage:
    def test_broken_image_returns_none(self):
        result, status = prepare_image(b"not an image")
        assert result is None
        assert status == "broken"

    def test_tiny_image_returns_none(self):
        img = Image.new("RGB", (50, 50))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result, status = prepare_image(buf.getvalue())
        assert result is None
        assert status == "tiny"

    def test_large_image_downscaled(self):
        img = Image.new("RGB", (3000, 2000))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result, status = prepare_image(buf.getvalue())
        assert result is not None
        assert status == "ok"
        assert max(result.width, result.height) <= 1600

    def test_small_image_upscaled(self):
        img = Image.new("RGB", (600, 400))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result, status = prepare_image(buf.getvalue())
        assert result is not None
        assert status == "ok"
        assert max(result.width, result.height) >= 1200

    def test_normal_image_unchanged(self):
        img = Image.new("RGB", (1500, 1000))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result, status = prepare_image(buf.getvalue())
        assert result is not None
        assert status == "ok"
        assert result.width == 1500
        assert result.height == 1000


class TestGetEngine:
    def test_singleton_pattern(self):
        with patch("rapidocr_onnxruntime.RapidOCR") as mock_ocr:
            mock_instance = Mock()
            mock_ocr.return_value = mock_instance
            engine1 = _get_engine()
            engine2 = _get_engine()
            assert engine1 is engine2
            mock_ocr.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])