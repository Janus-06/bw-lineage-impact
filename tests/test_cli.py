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


def test_collect_live_requires_explicit_read_only_confirmation(capsys, monkeypatch) -> None:
    monkeypatch.setenv("BWLI_LIVE", "1")

    assert app(["collect", "--live", "--search-term", "Z"]) == 2

    captured = capsys.readouterr()
    assert "--confirm-read-only" in captured.err
    assert "no BW calls" in captured.err


def test_collect_live_rejects_output_path_escape_before_bw_config(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("BWLI_LIVE", "1")

    assert (
        app(
            [
                "collect",
                "--live",
                "--confirm-read-only",
                "--search-term",
                "Z",
                "--out",
                "../outside",
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert "outside project root" in captured.err
    assert "BW_URL" not in captured.err
    assert not (tmp_path / "outside").exists()
