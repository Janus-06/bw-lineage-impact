from __future__ import annotations

from bwli import __version__
from bwli.cli import app


def test_version_cli(capsys) -> None:
    assert app(["--version"]) == 0

    captured = capsys.readouterr()
    assert f"bwli {__version__}" in captured.out


def test_stub_commands_are_safe_and_offline(capsys) -> None:
    for command in ["collect", "impact", "diff", "report"]:
        assert app([command]) == 0
        captured = capsys.readouterr()
        combined = f"{captured.out}\n{captured.err}".lower()
        assert "no bw calls" in combined or "offline" in combined or "stub" in combined
        assert "sap" not in combined


def test_collect_live_requires_explicit_gate(capsys, monkeypatch) -> None:
    monkeypatch.delenv("BWLI_LIVE", raising=False)

    assert app(["collect", "--live", "--search-term", "Z"]) == 2

    captured = capsys.readouterr()
    assert "gated" in captured.err
    assert "no BW calls" in captured.err
