from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_windows_launcher_keeps_the_local_secret_out_of_arguments_and_repository() -> None:
    launcher = (ROOT / "scripts/start-paper-mvp.ps1").read_text(encoding="utf-8")

    assert "Read-Host" in launcher
    assert "-AsSecureString" in launcher
    assert "GetTempPath" in launcher
    assert "[Guid]::NewGuid()" in launcher
    assert "bootstrap-local-operator.py" in launcher
    assert "--secret-file $secretPath" in launcher
    assert "--verifier-file $verifierPath" in launcher
    assert "Remove-Item -LiteralPath $secretPath" in launcher
    assert launcher.count("ZeroFreeBSTR") == 2
    assert "--password" not in launcher


def test_windows_launcher_uses_only_the_approved_paper_mvp_entry_points() -> None:
    launcher = (ROOT / "scripts/start-paper-mvp.ps1").read_text(encoding="utf-8")
    start_wrapper = (ROOT / "START_PAPER_MVP.cmd").read_text(encoding="utf-8")
    stop_wrapper = (ROOT / "STOP_PAPER_MVP.cmd").read_text(encoding="utf-8")

    assert "corepack pnpm bootstrap" in launcher
    assert "corepack pnpm env:init" in launcher
    assert "corepack pnpm paper:mvp:start" in launcher
    assert '$env:WOOZOO_OPEN_BROWSER = "1"' in launcher
    assert "start-paper-mvp.ps1" in start_wrapper
    assert "corepack pnpm paper:mvp:stop" in stop_wrapper
    assert "docker compose down -v" not in stop_wrapper


def test_windows_launcher_files_use_windows_safe_encodings() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    powershell_bytes = (ROOT / "scripts/start-paper-mvp.ps1").read_bytes()

    assert "*.cmd text eol=crlf" in attributes
    assert "*.ps1 text eol=crlf" in attributes
    assert powershell_bytes.startswith(b"\xef\xbb\xbf")
