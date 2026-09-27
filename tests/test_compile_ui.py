from subprocess import CompletedProcess

import pytest

from scripts import compile_ui


@pytest.mark.parametrize("failure_step", [None, 0, 1, 2])
def test_ui_generation_runs_normalization_and_fails_closed(
    monkeypatch, tmp_path, failure_step
):
    commands = []
    monkeypatch.setattr(compile_ui.shutil, "which", lambda name: name)
    monkeypatch.setattr(compile_ui, "_resolve_uic_command", lambda: ["pyside6-uic"])

    def run(command, **kwargs):
        step = len(commands)
        commands.append(command)
        return CompletedProcess(command, int(step == failure_step), "", "failure")

    monkeypatch.setattr(compile_ui.subprocess, "run", run)
    source, output = tmp_path / "screen.ui", tmp_path / "ui_screen.py"
    assert compile_ui.compile_ui_file(source, output) is (failure_step is None)
    expected = [
        ["pyside6-uic", "-g", "python", str(source), "-o", str(output)],
        ["ruff", "check", "--select", "F401", "--fix", str(output)],
        ["ruff", "format", str(output)],
    ]
    assert commands == expected[: 3 if failure_step is None else failure_step + 1]
