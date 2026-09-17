"""Pinned Python invocations emit UTF-8 even without inherited UTF-8 mode."""
import json
import os
import subprocess
import sys


def test_redirected_cli_handles_unicode_workspace_without_utf8_flag(tmp_path):
    root = tmp_path / "\u5ba2\u6237"
    root.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    process = subprocess.run([
        sys.executable, "-I", "-m", "mcp_openapi_creator_kit.cli",
        "--workspace", str(root), "init",
    ], env=env, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr.decode("utf-8")
    result = json.loads(process.stdout.decode("utf-8"))
    assert result["workspace"] == str(root)
    assert not result["writes"]
    assert not list(root.iterdir())
