"""Existing-release acceptance rejects incomplete or mismatched artifacts before execution."""
import importlib.util
from pathlib import Path
import sys

import pytest

PATH = Path(__file__).resolve().parents[1] / "wheel-smoke.py"
SPEC = importlib.util.spec_from_file_location("release_wheel_smoke", PATH)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


@pytest.mark.parametrize("arguments", [
    ["--wheel", "missing.whl"],
    ["--sdist", "missing.tar.gz"],
    ["--expected-sha256", "0" * 64],
    ["--wheel", "missing.whl", "--sdist", "missing.tar.gz"],
])
def test_incomplete_release_arguments_fail_before_writes(tmp_path, monkeypatch, arguments):
    output = tmp_path / "acceptance"
    monkeypatch.setattr(sys, "argv", [str(PATH), "--artifacts", str(output), *arguments])
    monkeypatch.setattr(smoke.subprocess, "run", lambda *_a, **_k: pytest.fail("Unexpected subprocess"))
    with pytest.raises(SystemExit) as error:
        smoke.main()
    assert error.value.code == 2
    assert not output.exists()


def test_mismatched_hash_never_installs_or_builds(tmp_path, monkeypatch):
    wheel, sdist = tmp_path / "release.whl", tmp_path / "release.tar.gz"
    wheel.write_bytes(b"changed")
    sdist.write_bytes(b"source")
    output = tmp_path / "acceptance"
    monkeypatch.setattr(sys, "argv", [
        str(PATH), "--artifacts", str(output), "--wheel", str(wheel), "--sdist", str(sdist),
        "--expected-sha256", "0" * 64,
    ])
    monkeypatch.setattr(smoke.subprocess, "run", lambda *_a, **_k: pytest.fail("Unexpected subprocess"))
    with pytest.raises(SystemExit) as error:
        smoke.main()
    assert error.value.code == 2
    assert not output.exists()
