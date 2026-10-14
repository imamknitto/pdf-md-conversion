from __future__ import annotations

import pytest

from app.converters.flowchart import (
    _label,
    _nearest_node,
    from_ocr_boxes,
    is_flowchart,
    mermaid_from_nodes,
)


class TestLabel:
    def test_truncates_long_text(self):
        text = "x" * 50
        result = _label(text, limit=40)
        assert len(result) == 40
        assert result.endswith("…")

    def test_replaces_quotes(self):
        assert _label('hello "world"') == "hello 'world'"

    def test_replaces_newlines(self):
        assert _label("hello\nworld") == "hello world"

    def test_strips_whitespace(self):
        assert _label("  hello  world  ") == "hello world"

    def test_empty_string(self):
        assert _label("") == ""

    def test_none_returns_empty(self):
        assert _label(None) == ""


class TestMermaidFromNodes:
    def test_empty_nodes_returns_empty_flowchart(self):
        result = mermaid_from_nodes([], [])
        assert result == "flowchart TD"

    def test_single_node(self):
        nodes = [{"text": "Start"}]
        result = mermaid_from_nodes(nodes, [])
        expected = 'flowchart TD\n  n1["Start"]'
        assert result == expected

    def test_multiple_nodes_with_edges(self):
        nodes = [{"text": "A"}, {"text": "B"}, {"text": "C"}]
        edges = [(0, 1, ""), (1, 2, "yes")]
        result = mermaid_from_nodes(nodes, edges)
        assert 'n1["A"]' in result
        assert 'n2["B"]' in result
        assert 'n3["C"]' in result
        assert "n1 --> n2" in result
        assert "n2 -->|yes| n3" in result

    def test_no_edges_creates_dotted_chain(self):
        nodes = [{"text": "A"}, {"text": "B"}]
        result = mermaid_from_nodes(nodes, [])
        assert "n1 -.-> n2" in result


class TestFromOCRBoxes:
    def test_empty_boxes_returns_none_confidence(self):
        result = from_ocr_boxes([])
        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["mermaid"] == ""
        assert result["confidence"] == "none"
        assert result["method"] == "ocr-reading-order"

    def test_single_box(self):
        boxes = [{"text": "Start", "cx": 100, "cy": 100, "conf": 0.9}]
        result = from_ocr_boxes(boxes)
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["text"] == "Start"
        assert result["confidence"] == "low"
        assert "Start" in result["mermaid"]

    def test_multiple_boxes_creates_linear_chain(self):
        boxes = [
            {"text": "Start", "cx": 100, "cy": 100, "conf": 0.9},
            {"text": "Process", "cx": 100, "cy": 200, "conf": 0.8},
            {"text": "End", "cx": 100, "cy": 300, "conf": 0.9},
        ]
        result = from_ocr_boxes(boxes)
        assert len(result["nodes"]) == 3
        assert len(result["edges"]) == 2
        assert result["edges"] == [(0, 1, ""), (1, 2, "")]
        assert "n1 --> n2" in result["mermaid"]
        assert "n2 --> n3" in result["mermaid"]


class TestNearestNode:
    def test_finds_nearest(self):
        nodes = [
            {"cx": 100, "cy": 100},
            {"cx": 200, "cy": 200},
            {"cx": 300, "cy": 300},
        ]
        idx = _nearest_node(nodes, 110, 110)
        assert idx == 0

    def test_returns_minus_one_when_out_of_tolerance(self):
        nodes = [{"cx": 100, "cy": 100}]
        idx = _nearest_node(nodes, 500, 500, tol=40)
        assert idx == -1

    def test_empty_nodes_returns_minus_one(self):
        idx = _nearest_node([], 100, 100)
        assert idx == -1


class TestIsFlowchart:
    def test_empty_boxes_returns_false(self):
        result = is_flowchart([])
        assert result["yes"] is False
        assert result["score"] == 0

    def test_numbered_steps_increases_score(self):
        boxes = [
            {"text": "1. First step", "cx": 100, "cy": 100},
            {"text": "2. Second step", "cx": 100, "cy": 200},
        ]
        result = is_flowchart(boxes)
        assert result["score"] >= 2
        assert any("langkah bernomor" in r for r in result["reasons"])

    def test_flow_keywords_increases_score(self):
        boxes = [
            {"text": "Jika kondisi", "cx": 100, "cy": 100},
            {"text": "Maka proses", "cx": 100, "cy": 200},
        ]
        result = is_flowchart(boxes)
        assert result["score"] >= 1
        assert any("kata alur" in r for r in result["reasons"])

    def test_y_n_labels_increases_score(self):
        boxes = [
            {"text": "Ya", "cx": 100, "cy": 100},
            {"text": "Tidak", "cx": 200, "cy": 100},
        ]
        result = is_flowchart(boxes)
        assert result["score"] >= 1
        assert any("label cabang" in r for r in result["reasons"])

    def test_many_text_lines_increases_score(self):
        boxes = [{"text": f"Line {i}", "cx": 100, "cy": i * 50} for i in range(5)]
        result = is_flowchart(boxes)
        assert result["score"] >= 1
        assert any("5 baris teks" in r for r in result["reasons"])

    def test_weak_signals_not_enough(self):
        boxes = [{"text": "1", "cx": 100, "cy": 100}, {"text": "2", "cx": 200, "cy": 100}]
        result = is_flowchart(boxes)
        assert result["yes"] is False

    def test_strong_signal_required(self):
        boxes = [
            {"text": "1. Mulai", "cx": 100, "cy": 100},
            {"text": "2. Proses", "cx": 100, "cy": 200},
        ]
        result = is_flowchart(boxes)
        assert result["yes"] is True

    def test_exception_handling(self):
        class BadBoxes:
            def __iter__(self):
                raise ValueError("bad")

        result = is_flowchart(BadBoxes())
        assert result["yes"] is False
        assert result["score"] == 0
        assert any("gagal skor" in r for r in result["reasons"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])