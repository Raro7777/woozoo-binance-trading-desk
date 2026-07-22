from __future__ import annotations

import pytest

from evidence_worker.settings import EvidenceSettings, EvidenceSettingsError
from evidence_worker.app import create_evidence_app


def test_evidence_settings_require_paper_mode_and_dedicated_database_url() -> None:
    environment = {
        "TRADING_MODE": "paper",
        "EVIDENCE_DATABASE_URL": "postgresql://woozoo_evidence_writer@postgres/woozoo",
    }

    assert (
        EvidenceSettings.from_environment(environment).database_url
        == environment["EVIDENCE_DATABASE_URL"]
    )
    with pytest.raises(EvidenceSettingsError, match="TRADING_MODE=paper"):
        EvidenceSettings.from_environment({**environment, "TRADING_MODE": "live"})
    with pytest.raises(EvidenceSettingsError, match="required"):
        EvidenceSettings.from_environment({"TRADING_MODE": "paper"})
    with pytest.raises(EvidenceSettingsError, match="dedicated"):
        EvidenceSettings.from_environment(
            {"TRADING_MODE": "paper", "EVIDENCE_DATABASE_URL": "postgresql://postgres/db"}
        )
    with pytest.raises(EvidenceSettingsError, match="TRADING_MODE=paper"):
        create_evidence_app({**environment, "TRADING_MODE": "live"})
    with pytest.raises(EvidenceSettingsError, match="required"):
        create_evidence_app({"TRADING_MODE": "paper"})
