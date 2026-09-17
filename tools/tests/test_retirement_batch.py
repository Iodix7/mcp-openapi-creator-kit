"""Local Windows az.cmd argument transport and redacted failure diagnostics."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_retirement import BASE, SUB, rc


@pytest.fixture
def fake_az_cmd(tmp_path, monkeypatch):
    directory = tmp_path / "CLI (fixture)&100%"
    directory.mkdir()
    launcher = directory / "az.cmd"
    (directory / "argv.py").write_text(
        "import json,sys; print(json.dumps(sys.argv[1:], ensure_ascii=True))\n", encoding="utf-8")
    launcher.write_bytes(
        b'@IF EXIST "%MCP_KIT_TEST_PYTHON%" (\r\n'
        b'  "%MCP_KIT_TEST_PYTHON%" -I "%~dp0argv.py" %*\r\n'
        b') ELSE (\r\n  exit /b 1\r\n)\r\n')
    monkeypatch.setenv("MCP_KIT_TEST_PYTHON", sys._base_executable)
    monkeypatch.setenv("MCP_KIT_TEST_SECRET", "SHOULD_NOT_EXPAND")
    monkeypatch.setattr(rc, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rc.shutil, "which", lambda _: str(launcher))
    return launcher


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows batch parsing")
def test_reproduces_unquoted_if_block_query_failure(fake_az_cmd, tmp_path):
    plain = subprocess.run([str(fake_az_cmd), "rest"], cwd=tmp_path, capture_output=True, timeout=10)
    assert plain.returncode == 0 and json.loads(plain.stdout) == ["rest"]
    expression = "{count:length(value),first:value[:2],nextLink:nextLink}"
    result = subprocess.run([str(fake_az_cmd), "rest", "--query", expression],
                            cwd=tmp_path, capture_output=True, timeout=10)
    assert result.returncode != 0


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows batch parsing")
def test_actual_batch_receives_exact_query_and_metacharacters_without_injection(fake_az_cmd, tmp_path):
    arguments = [
        "rest", "--query", "{count:length(value),first:value[:2],nextLink:nextLink}",
        "--uri", BASE + "/subscriptions?api-version=2024-05-01&$skiptoken=a%2Fb==",
        "literal&pipe|redirect<>()^", "%MCP_KIT_TEST_SECRET%", "!MCP_KIT_TEST_SECRET!",
        "x & echo INJECTED > injected.txt", "trailing\\", "",
    ]
    assert json.loads(rc.run(["az", *arguments], capture=True)) == arguments
    assert not (tmp_path / "injected.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows batch parsing")
def test_subscription_projection_round_trips_through_actual_batch(fake_az_cmd):
    calls = []
    client = rc.RetirementClient(
        SUB, "fixture", "fixture-apim",
        runner=lambda args: calls.append(args) or '{"value":[],"count":0}')
    client.records("/subscriptions")
    arguments = calls[0][1:]
    assert "--query" in arguments
    assert json.loads(rc.run(["az", *arguments], capture=True)) == arguments


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows batch parsing")
def test_exact_subscription_selector_is_preserved_by_both_launch_forms(fake_az_cmd, tmp_path):
    calls = []
    client = rc.RetirementClient(
        SUB, "fixture", "fixture-apim",
        runner=lambda args: calls.append(args) or '{"value":[],"count":0}')
    client.records("/subscriptions")
    arguments = calls[0][1:]
    original = subprocess.run([str(fake_az_cmd), *arguments],
                              cwd=tmp_path, capture_output=True, timeout=10)
    assert original.returncode == 0 and json.loads(original.stdout) == arguments
    assert json.loads(rc.run(["az", *arguments], capture=True)) == arguments


@pytest.mark.skipif(os.name != "nt", reason="Windows batch argument safety")
@pytest.mark.parametrize("argument", ['x" & echo INJECTED > injected.txt', "line\nbreak", "line\rbreak", "\0"])
def test_batch_quotes_and_controls_are_rejected_before_launch(fake_az_cmd, monkeypatch, argument, tmp_path):
    monkeypatch.setattr(rc.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Unsafe batch was launched"))
    with pytest.raises(rc.ReconcileError, match="unsupported"):
        rc.run(["az", "rest", "--query", argument], capture=True)
    assert not (tmp_path / "injected.txt").exists()


@pytest.mark.parametrize("stderr,expected", [
    (b'ERROR: (AuthorizationFailed) PRIVATE_MESSAGE_WITH_KEYS', "AuthorizationFailed"),
    (b'ERROR: {"error":{"code":"InvalidApiVersionParameter","message":"PRIVATE_KEY"}}', "InvalidApiVersionParameter"),
    (b'{"error":{"code":"Forbidden","details":[{"message":"PRIVATE_KEY"}]}}', "Forbidden"),
    (b'ERROR: Bad Request({"error":{"code":"MethodNotAllowedInPricingTier",'
     b'"message":"Method not allowed in Consumption pricing tier PRIVATE_KEY","details":null}})',
     "MethodNotAllowedInPricingTier"),
    (b'ERROR: Bad Request({"error":{"code":"PRIVATE\\nKEY","message":"PRIVATE_MESSAGE"}})', "unavailable"),
    (b'ERROR: Bad Request({"error":{"code":"Forbidden","message":"PRIVATE_MESSAGE"}}) PRIVATE_TAIL', "unavailable"),
    (b'ERROR: syntax error PRIVATE_KEY', "unavailable"),
    (b'{"error":{"code":"PRIVATE\\nKEY","message":"PRIVATE_MESSAGE"}}', "unavailable"),
    (b'\x81 PRIVATE_KEY', "unavailable"),
])
def test_failure_shows_stage_exit_and_parsed_arm_code_only(monkeypatch, tmp_path, capsys, stderr, expected):
    monkeypatch.setattr(rc, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rc.shutil, "which", lambda _: sys.executable)
    process = SimpleNamespace(
        returncode=255, stdout=io.BytesIO(), stderr=io.BytesIO(),
        communicate=lambda **kwargs: (b"PRIVATE_STDOUT", stderr))
    monkeypatch.setattr(rc.subprocess, "Popen", lambda *args, **kwargs: process)
    with pytest.raises(rc.ReconcileError) as error:
        rc.run(["az", "rest", "--method", "GET",
                "--uri", BASE + "/subscriptions?api-version=PRIVATE_QUERY"], capture=True)
    text = str(error.value) + capsys.readouterr().err
    assert "CLI failed GET" in text and "exit 255" in text
    assert "ARM code " + expected in text
    assert BASE + "/subscriptions" in text
    assert "PRIVATE" not in text
