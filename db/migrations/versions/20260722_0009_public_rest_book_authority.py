"""Allow only provenance-complete Binance public REST books into Paper authority."""

from alembic import op


revision = "20260722_0009"
down_revision = "20260722_0008"
branch_labels = None
depends_on = None

_STREAM_ONLY = "AND raw.record_kind='stream_message'"
_PROVENANCE_COMPLETE = (
    "AND (raw.record_kind='stream_message' OR paper_public_rest_book_lineage_is_exact_v1(raw.id))"
)


def _replace_authority_marker(before: str, after: str) -> None:
    before_sql = "'" + before.replace("'", "''") + "'"
    after_sql = "'" + after.replace("'", "''") + "'"
    op.execute(
        f"""
        DO $migration$
        DECLARE
          definition text;
        BEGIN
          definition:=pg_get_functiondef(
            'paper_recorded_book_market_is_current_v1(varchar)'::regprocedure);
          IF position({before_sql} in definition)=0 THEN
            RAISE EXCEPTION 'recorded-book authority marker is missing';
          END IF;
          definition:=replace(definition,{before_sql},{after_sql});
          EXECUTE definition;
        END
        $migration$;
        """
    )


def upgrade() -> None:
    op.execute(r"""
        CREATE FUNCTION paper_public_rest_book_lineage_is_exact_v1(p_raw_event_id varchar)
        RETURNS boolean AS $$
          SELECT EXISTS (
            SELECT 1
            FROM raw_market_events derived
            JOIN raw_market_events item ON item.id=derived.parent_raw_event_id
            JOIN raw_market_events response ON response.id=item.parent_raw_event_id
            WHERE derived.id=p_raw_event_id
              AND derived.record_kind='rest_item'
              AND derived.parent_raw_event_id IS NOT NULL
              AND item.parent_raw_event_id IS NOT NULL
              AND item.collector_session_id=derived.collector_session_id
              AND response.collector_session_id=derived.collector_session_id
              AND derived.stream=lower(derived.symbol)||'@bookTicker'
              AND derived.source_event_time IS NULL
              AND derived.received_at=item.received_at
              AND derived.received_at=response.received_at
              AND derived.sequence=floor(extract(epoch FROM derived.received_at)*1000)::bigint
              AND encode(digest(derived.payload_bytes,'sha256'),'hex')=derived.payload_hash
              AND item.source=derived.source
              AND item.symbol=derived.symbol
              AND item.record_kind='rest_item'
              AND item.stream='rest-item:/api/v3/ticker/bookTicker:0'
              AND item.source_event_time IS NULL
              AND item.sequence IS NULL
              AND encode(digest(item.payload_bytes,'sha256'),'hex')=item.payload_hash
              AND response.source=derived.source
              AND response.symbol=derived.symbol
              AND response.record_kind='rest_response'
              AND response.stream='rest:/api/v3/ticker/bookTicker'
              AND response.parent_raw_event_id IS NULL
              AND response.source_event_time IS NULL
              AND response.sequence IS NULL
              AND encode(digest(response.payload_bytes,'sha256'),'hex')=response.payload_hash
              AND convert_from(response.payload_bytes,'UTF8')::jsonb=
                  convert_from(item.payload_bytes,'UTF8')::jsonb
              AND jsonb_typeof(convert_from(item.payload_bytes,'UTF8')::jsonb)='object'
              AND convert_from(item.payload_bytes,'UTF8')::jsonb ?&
                  array['symbol','bidPrice','bidQty','askPrice','askQty']
              AND convert_from(item.payload_bytes,'UTF8')::jsonb-
                  array['symbol','bidPrice','bidQty','askPrice','askQty']='{}'::jsonb
              AND convert_from(item.payload_bytes,'UTF8')::jsonb->>'symbol'=derived.symbol
              AND jsonb_typeof(
                    convert_from(item.payload_bytes,'UTF8')::jsonb->'bidPrice')='string'
              AND jsonb_typeof(
                    convert_from(item.payload_bytes,'UTF8')::jsonb->'bidQty')='string'
              AND jsonb_typeof(
                    convert_from(item.payload_bytes,'UTF8')::jsonb->'askPrice')='string'
              AND jsonb_typeof(
                    convert_from(item.payload_bytes,'UTF8')::jsonb->'askQty')='string'
              AND jsonb_typeof(convert_from(derived.payload_bytes,'UTF8')::jsonb)='object'
              AND convert_from(derived.payload_bytes,'UTF8')::jsonb ?&
                  array['u','s','b','B','a','A']
              AND convert_from(derived.payload_bytes,'UTF8')::jsonb->>'s'=derived.symbol
              AND convert_from(derived.payload_bytes,'UTF8')::jsonb->>'u'=
                  derived.sequence::text
              AND convert_from(derived.payload_bytes,'UTF8')::jsonb->>'b'=
                  convert_from(item.payload_bytes,'UTF8')::jsonb->>'bidPrice'
              AND convert_from(derived.payload_bytes,'UTF8')::jsonb->>'B'=
                  convert_from(item.payload_bytes,'UTF8')::jsonb->>'bidQty'
              AND convert_from(derived.payload_bytes,'UTF8')::jsonb->>'a'=
                  convert_from(item.payload_bytes,'UTF8')::jsonb->>'askPrice'
              AND convert_from(derived.payload_bytes,'UTF8')::jsonb->>'A'=
                  convert_from(item.payload_bytes,'UTF8')::jsonb->>'askQty'
          )
        $$ LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION paper_public_rest_book_lineage_is_exact_v1(varchar) FROM PUBLIC"
    )
    _replace_authority_marker(_STREAM_ONLY, _PROVENANCE_COMPLETE)


def downgrade() -> None:
    _replace_authority_marker(_PROVENANCE_COMPLETE, _STREAM_ONLY)
    op.execute("DROP FUNCTION paper_public_rest_book_lineage_is_exact_v1(varchar)")
