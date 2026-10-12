from __future__ import annotations

from tests.conftest import sample_bytes


def _convert(client) -> dict:
    r = client.post(
        "/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
    )
    assert r.status_code == 200
    return r.json()


def test_documents_lists_converted(client):
    data = _convert(client)
    r = client.get("/documents")
    assert r.status_code == 200
    docs = r.json()["documents"]
    assert any(d["doc_id"] == data["doc_id"] for d in docs)


def test_download_returns_markdown(client):
    data = _convert(client)
    r = client.get(f"/download/{data['doc_id']}")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert r.text.startswith("## Page 1")


def test_download_unknown_404(client):
    r = client.get("/download/" + "b" * 32)
    assert r.status_code == 404


def test_delete_document(client):
    data = _convert(client)
    r = client.delete(f"/documents/{data['doc_id']}")
    assert r.status_code == 200
    assert r.json() == {"doc_id": data["doc_id"], "deleted": True}
    assert client.get(f"/download/{data['doc_id']}").status_code == 404


def test_delete_unknown_404(client):
    r = client.delete("/documents/" + "c" * 32)
    assert r.status_code == 404
