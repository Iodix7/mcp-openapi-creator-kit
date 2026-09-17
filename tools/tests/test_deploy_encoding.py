import importlib.util
from pathlib import Path
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("deploy_encoding", TOOLS / "deploy-client.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Azure CLI uses its bundled isolated Python")
def test_azure_output_uses_os_locale_in_utf8_host(monkeypatch):
    monkeypatch.setattr(deploy.locale, "getencoding", lambda: "cp1252")
    monkeypatch.setattr(deploy.shutil, "which", lambda _: sys.executable)
    output = deploy.run([
        "az", "-I", "-c", r"import sys; sys.stdout.buffer.write(b'{\"name\":\"Demo \x97 API\"}')",
    ], capture=True)
    assert output == '{"name":"Demo \u2014 API"}'


def test_python_output_retains_host_encoding(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _: sys.executable)
    output = deploy.run([
        sys.executable, "-I", "-c", "print('generator complete')",
    ], capture=True)
    assert output.strip() == "generator complete"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Azure CLI uses its bundled isolated Python")
def test_invalid_azure_encoding_fails_without_silent_pipe_error(monkeypatch, capsys):
    monkeypatch.setattr(deploy.locale, "getencoding", lambda: "cp1252")
    monkeypatch.setattr(deploy.shutil, "which", lambda _: sys.executable)
    with pytest.raises(deploy.ReconcileError, match="could not be decoded"):
        deploy.run(["az", "-I", "-c", r"import sys; sys.stdout.buffer.write(b'secret\x81')"], capture=True)
    captured = capsys.readouterr()
    assert "secret" not in captured.out + captured.err
