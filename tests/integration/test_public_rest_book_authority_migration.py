from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_public_rest_book_authority_requires_complete_immutable_lineage() -> None:
    source = (
        ROOT / "db/migrations/versions/20260722_0009_public_rest_book_authority.py"
    ).read_text(encoding="utf-8")

    assert "paper_public_rest_book_lineage_is_exact_v1" in source
    assert "raw.record_kind='stream_message' OR" in source
    assert "item.id=derived.parent_raw_event_id" in source
    assert "response.id=item.parent_raw_event_id" in source
    assert "encode(digest(derived.payload_bytes,'sha256'),'hex')=derived.payload_hash" in source
    assert "encode(digest(item.payload_bytes,'sha256'),'hex')=item.payload_hash" in source
    assert "encode(digest(response.payload_bytes,'sha256'),'hex')=response.payload_hash" in source
    assert "response.payload_bytes,'UTF8')::jsonb=" in source
    assert "item.payload_bytes,'UTF8')::jsonb" in source
    assert "bidPrice" in source and "askPrice" in source
    assert "REVOKE ALL ON FUNCTION paper_public_rest_book_lineage_is_exact_v1" in source
