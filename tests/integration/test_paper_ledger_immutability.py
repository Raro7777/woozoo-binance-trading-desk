from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from paper_engine import PaperEngine
from paper_engine.models import LedgerEntry


def test_fin_004_posted_journal_is_immutable_and_correction_is_reversal_replacement() -> None:
    engine = PaperEngine()
    engine.seed_balance("USDT", "10", seed_id="seed")
    original_key, original = next(iter(engine.journals.items()))
    with pytest.raises(FrozenInstanceError):
        original.journal_kind = "VALUATION"  # type: ignore[misc]
    reversal, replacement = engine.reverse_and_replace(
        original_key=original_key,
        correction_id="correction-1",
        replacement_entries=(
            LedgerEntry("paper.available", "USDT", Decimal("9"), Decimal(0)),
            LedgerEntry("paper.opening-equity", "USDT", Decimal(0), Decimal("9")),
        ),
    )
    assert reversal.reversal_of == original.journal_id
    assert replacement.replacement_for == original.journal_id
    reversal.assert_balanced()
    replacement.assert_balanced()
