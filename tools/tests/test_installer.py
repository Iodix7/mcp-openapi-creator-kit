"""Preview/safety coverage plus an opt-in real, fully offline wheel installation."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import ssl
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("kit_installer_tests", ROOT / "install-kit.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def make_wheel(directory, *, version="1.2.3", name=installer.DISTRIBUTION,
               requirement=">=3.12", extra=None):
    wheel = directory / f"mcp_openapi_creator_kit-{version}-py3-none-any.whl"
    guidance = {"AGENTS.md": b"constitution", "skills/discovery.md": b"discover",
                "skills/onboarding.md": b"onboard", "skills/lifecycle.md": b"lifecycle"}
    manifest = {key: hashlib.sha256(value).hexdigest() for key, value in guidance.items()}
    manifest.update({"package/plugin.py": hashlib.sha256(b"plugin").hexdigest(),
                     "package/cli.py": hashlib.sha256(b"cli").hexdigest()})
    prefix = f"mcp_openapi_creator_kit-{version}.dist-info"
    files = {
        f"{prefix}/METADATA": f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\nRequires-Python: {requirement}\n",
        f"{prefix}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "mcp_openapi_creator_kit/assets/manifest.json": json.dumps(manifest),
        "mcp_openapi_creator_kit/plugin.py": b"plugin",
        "mcp_openapi_creator_kit/cli.py": b"cli",
        **{f"mcp_openapi_creator_kit/assets/{key}": value for key, value in guidance.items()},
        **(extra or {}),
    }
    with zipfile.ZipFile(wheel, "w") as archive:
        for key, value in files.items():
            archive.writestr(key, value)
    return wheel


@pytest.fixture
def arguments(tmp_path):
    customer = tmp_path / "Customer with spaces"
    customer.mkdir()
    (customer / "keep.txt").write_text("untouched", encoding="utf-8")
    wheel = make_wheel(tmp_path)
    return installer.parser().parse_args([
        "--wheel", str(wheel), "--sha256", installer.digest(wheel),
        "--workspace", str(customer), "--install-dir", str(tmp_path / "Runtime with spaces"),
        "--plugin-dir", str(tmp_path / "Plugin with spaces"),
    ])


def fake_runtime(monkeypatch, arguments, *, failure=None, wrong_provenance=False, wrong_connection=False):
    calls = []
    expected = installer.plan(arguments)
    runtime = Path(expected["installDirectory"])
    python = Path(expected["commands"]["installWheel"][0])
    kit = {"version": expected["release"]["version"], "source": "installed-package",
           "manifestSha256": expected["release"]["manifestSha256"], "verifiedFiles": 4}
    probe = {
        "kit": {**kit, "source": "source-development"} if wrong_provenance else kit,
        "prefix": str(runtime), "basePrefix": str(runtime.parent / "base"),
        "executable": str(python), "package": str(runtime / "site-packages" / "package" / "__init__.py"),
        "python": [3, 12, 10], "distributions": {installer.DISTRIBUTION: kit["version"]},
    }

    def run(command, *, cwd, label):
        calls.append((command, cwd, label))
        if failure == label:
            raise installer.InstallError(f"{label} failed")
        if "-m" in command and command[command.index("-m") + 1] == "venv":
            python.parent.mkdir()
            python.write_text("fake Python", encoding="utf-8")
            (runtime / "pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")
        elif "-c" in command:
            return json.dumps(probe)
        elif "plugin-export" in command:
            plugin = arguments.plugin_dir
            plugin.mkdir()
            hashes = {}
            for name in ("AGENTS.md", "skills/discovery.md", "skills/onboarding.md", "skills/lifecycle.md"):
                relative = f"skills/create-mcp/references/{name}"
                path = plugin / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name, encoding="utf-8")
                hashes[relative] = installer.digest(path)
            connection = {
                "kit": kit, "workspace": str(arguments.workspace),
                "interpreter": str(python),
                "cliPrefix": [str(python), "-I", "-m", "mcp_openapi_creator_kit.cli",
                              "--workspace", str(arguments.workspace)],
                "guidanceSha256": hashes,
            }
            (plugin / "connection.json").write_text(json.dumps(connection), encoding="utf-8")
            (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {
                installer.PLUGIN: {"command": "python" if wrong_connection else str(python),
                                   "args": ["-I", "-m", "mcp_openapi_creator_kit",
                                            "--workspace", str(arguments.workspace)]}}}), encoding="utf-8")
            return '{"writes": true}'
        return ""

    monkeypatch.setattr(installer, "run", run)
    return calls


def test_preview_verifies_wheel_but_never_writes_or_starts_processes(arguments, monkeypatch):
    before = snapshot(arguments.workspace.parent)

    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must be read-only")

    monkeypatch.setattr(installer.subprocess, "run", forbidden)
    for method in ("mkdir", "write_bytes", "write_text", "touch", "rename", "replace"):
        monkeypatch.setattr(Path, method, forbidden)
    result = installer.install(arguments)
    assert result["status"] == "preview" and not result["writes"] and not result["hostActivated"]
    assert snapshot(arguments.workspace.parent) == before
    assert result["commands"]["createVenv"][:4] == [sys.executable, "-I", "-m", "venv"]
    assert result["host"]["vscodeSettings"] == {"chat.pluginLocations": {str(arguments.plugin_dir): True}}
    assert result["host"]["copilotCli"]["arguments"][-1] == str(arguments.plugin_dir)


@pytest.mark.parametrize("value", ["nope", "f" * 64])
def test_wrong_checksum_refused_before_any_output(arguments, value):
    arguments.sha256 = value
    with pytest.raises(installer.InstallError, match="SHA-256"):
        installer.install(arguments)
    assert not arguments.install_dir.exists() and not arguments.plugin_dir.exists()


@pytest.mark.parametrize("kwargs", [
    {"name": "wrong-kit"}, {"requirement": ">=99"}, {"requirement": "garbage"},
    {"extra": {"../escape": "unsafe"}}, {"extra": {"C:/escape": "unsafe"}},
    {"extra": {"mcp_openapi_creator_kit/assets/AGENTS.md": "changed"}},
    {"extra": {"mcp_openapi_creator_kit/assets/manifest.json": "{}"}},
])
def test_wrong_or_damaged_wheel_refused(arguments, kwargs):
    wheel = make_wheel(arguments.wheel.parent, **kwargs)
    arguments.sha256 = installer.digest(wheel)
    with pytest.raises(installer.InstallError):
        installer.install(arguments)
    assert not arguments.install_dir.exists()


def test_wrong_wheel_filename_refused(arguments):
    wheel = arguments.wheel.with_name("unrelated-1.2.3-py3-none-any.whl")
    arguments.wheel.rename(wheel)
    arguments.wheel = wheel
    with pytest.raises(installer.InstallError, match="filename"):
        installer.install(arguments)


def test_checksum_file_matches_exact_filename_once(arguments):
    checksum = arguments.wheel.parent / "SHA256SUMS"
    line = f"{arguments.sha256}  {arguments.wheel.name}\n"
    checksum.write_text(f"{'0' * 64}  install-kit.py\n{line}", encoding="utf-8")
    arguments.sha256_file, arguments.sha256 = checksum, None
    assert installer.install(arguments)["status"] == "preview"
    checksum.write_text(line + line, encoding="utf-8")
    with pytest.raises(installer.InstallError, match="exactly one"):
        installer.install(arguments)


@pytest.mark.parametrize("field", ["install_dir", "plugin_dir"])
def test_never_overwrites_existing_output_even_empty(arguments, field):
    getattr(arguments, field).mkdir()
    before = snapshot(arguments.workspace.parent)
    with pytest.raises(installer.InstallError, match="existing output"):
        installer.install(arguments)
    assert snapshot(arguments.workspace.parent) == before


@pytest.mark.parametrize("field", ["install_dir", "plugin_dir"])
def test_customer_overlap_rejected(arguments, field):
    setattr(arguments, field, arguments.workspace / "new-output")
    with pytest.raises(installer.InstallError, match="overlap"):
        installer.install(arguments)


def test_install_and_export_must_be_separate(arguments):
    arguments.plugin_dir = arguments.install_dir
    with pytest.raises(installer.InstallError, match="overlap"):
        installer.install(arguments)


def test_parent_must_exist_and_paths_must_be_absolute(arguments):
    arguments.install_dir = Path("relative")
    with pytest.raises(installer.InstallError, match="absolute"):
        installer.install(arguments)
    arguments.install_dir = arguments.workspace.parent / "missing" / "runtime"
    with pytest.raises(installer.InstallError, match="parent"):
        installer.install(arguments)


def test_dotdot_and_symlink_ancestors_are_refused(arguments):
    arguments.plugin_dir = arguments.workspace / ".." / "plugin"
    with pytest.raises(installer.InstallError, match="without"):
        installer.install(arguments)
    alias = arguments.workspace.parent / "alias"
    try:
        alias.symlink_to(arguments.workspace, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is not available for this Windows account")
    arguments.plugin_dir = alias / "new"
    with pytest.raises(installer.InstallError, match="symlink/junction"):
        installer.install(arguments)


def test_junction_ancestor_is_refused(arguments, monkeypatch):
    real = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == arguments.workspace.parent or real(path))
    with pytest.raises(installer.InstallError, match="symlink/junction"):
        installer.install(arguments)


def test_outputs_cannot_be_nested_in_previous_runtime(arguments):
    old = arguments.workspace.parent / "old-runtime"
    old.mkdir()
    (old / "pyvenv.cfg").write_text("old", encoding="utf-8")
    arguments.install_dir = old / "upgrade"
    with pytest.raises(installer.InstallError, match="existing runtime"):
        installer.install(arguments)
    assert snapshot(old) == {"pyvenv.cfg": b"old"}


@pytest.mark.parametrize("field", ["install_dir", "plugin_dir"])
def test_outputs_cannot_be_nested_in_previous_plugin(arguments, field):
    old = arguments.workspace.parent / "old-plugin"
    old.mkdir()
    for name in ("plugin.json", ".mcp.json"):
        (old / name).write_text("{}", encoding="utf-8")
    setattr(arguments, field, old / "new-output")
    with pytest.raises(installer.InstallError, match="existing plugin export"):
        installer.install(arguments)
    assert snapshot(old) == {"plugin.json": b"{}", ".mcp.json": b"{}"}


def test_wrong_python_has_no_fallback(arguments, monkeypatch):
    monkeypatch.setattr(installer.sys, "version_info", (3, 11, 11))
    with pytest.raises(installer.InstallError, match="Python >=3.12"):
        installer.install(arguments)


def test_apply_has_exact_array_invocations_and_validated_receipt(arguments, monkeypatch):
    before = snapshot(arguments.workspace)
    calls = fake_runtime(monkeypatch, arguments)
    arguments.apply = True
    result = installer.install(arguments)
    assert snapshot(arguments.workspace) == before
    assert result["status"] == "installed" and not result["hostActivated"]
    assert json.loads((arguments.install_dir / "installation.json").read_text("utf-8")) == result
    assert not (arguments.install_dir / "INSTALLATION-INCOMPLETE").exists()
    assert len(calls) == 5
    assert all(isinstance(command, list) for command, _, _ in calls)
    assert calls[1][0][0] == str(arguments.install_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    assert "--only-binary=:all:" in calls[1][0] and "--no-cache-dir" in calls[1][0]
    assert all(cwd != arguments.workspace for _, cwd, _ in calls)
    assert all(command[0] != "copilot" for command, _, _ in calls)


@pytest.mark.parametrize("failure", [
    "Dedicated venv creation", "Wheel/dependency installation",
    "Installed dependency validation", "Installed asset verification", "Plugin export",
])
def test_failure_never_writes_success_or_changes_customer(arguments, monkeypatch, failure):
    before = snapshot(arguments.workspace)
    fake_runtime(monkeypatch, arguments, failure=failure)
    arguments.apply = True
    with pytest.raises(installer.InstallError, match="failed"):
        installer.install(arguments)
    assert snapshot(arguments.workspace) == before
    assert not (arguments.install_dir / "installation.json").exists()
    assert (arguments.install_dir / "INSTALLATION-INCOMPLETE").exists()
    assert not arguments.plugin_dir.exists()


@pytest.mark.parametrize("fault", ["wrong_provenance", "wrong_connection"])
def test_wrong_installed_identity_has_no_completed_receipt(arguments, monkeypatch, fault):
    fake_runtime(monkeypatch, arguments, **{fault: True})
    arguments.apply = True
    with pytest.raises(installer.InstallError, match="provenance|pinned"):
        installer.install(arguments)
    assert not (arguments.install_dir / "installation.json").exists()


def test_upgrade_is_side_by_side_and_preserves_prior_runtime(arguments, monkeypatch):
    previous_runtime = arguments.workspace.parent / "old-runtime"
    previous_plugin = arguments.workspace.parent / "old-plugin"
    for path in (previous_runtime, previous_plugin):
        path.mkdir()
        (path / "keep").write_text("old release", encoding="utf-8")
    fake_runtime(monkeypatch, arguments)
    arguments.apply = True
    installer.install(arguments)
    assert snapshot(previous_runtime) == {"keep": b"old release"}
    assert snapshot(previous_plugin) == {"keep": b"old release"}
    assert snapshot(arguments.workspace) == {"keep.txt": b"untouched"}


def test_environment_does_not_inherit_python_or_pip_configuration(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "customer-code")
    monkeypatch.setenv("PIP_INDEX_URL", "https://user:secret@untrusted.example")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://untrusted.example")
    for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.setenv(name, "unselected certificate configuration")
    env = installer.clean_environment()
    assert "PYTHONPATH" not in env and "PIP_INDEX_URL" not in env and "PIP_EXTRA_INDEX_URL" not in env
    assert env["PIP_CONFIG_FILE"] == os.devnull
    assert not any(name in env for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"))


def test_subprocess_errors_do_not_echo_credentials(arguments, monkeypatch, capsys):
    authenticated_index = "https://user:private-index-token@example.invalid/simple"
    authenticated_proxy = "https://user:private-proxy-token@example.invalid:443"
    monkeypatch.setenv("PIP_INDEX_URL", authenticated_index)
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", authenticated_index)
    monkeypatch.setenv("HTTPS_PROXY", authenticated_proxy)

    def failed(command, **kwargs):
        assert kwargs["shell"] is False
        assert "PIP_INDEX_URL" not in kwargs["env"]
        assert "PIP_EXTRA_INDEX_URL" not in kwargs["env"]
        assert kwargs["env"]["PIP_CONFIG_FILE"] == os.devnull
        return subprocess.CompletedProcess(command, 1, authenticated_index, authenticated_proxy)

    monkeypatch.setattr(installer.subprocess, "run", failed)
    with pytest.raises(installer.InstallError) as caught:
        installer.run(["explicit-python", "-I", "-m", "pip"], cwd=arguments.workspace, label="Install")
    assert "private-" not in str(caught.value) and "exit 1" in str(caught.value)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("diagnostic,category", [
    ("SSLV3_ALERT_HANDSHAKE_FAILURE", "tls-handshake"),
    ("CERTIFICATE_VERIFY_FAILED", "tls-certificate-verification"),
])
def test_tls_failure_category_is_actionable_without_exposing_logs(arguments, monkeypatch, diagnostic, category):
    secret = "https://user:private-index-token@example.invalid/simple"
    monkeypatch.setattr(installer.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 1, secret, diagnostic + " " + secret))
    with pytest.raises(installer.InstallError) as caught:
        installer.run(["explicit-python", "-I", "-m", "pip"], cwd=arguments.workspace, label="Wheel install")
    assert caught.value.category == category
    assert "private-index-token" not in str(caught.value)
    assert "Do not disable TLS verification" in str(caught.value)


@pytest.mark.parametrize("version,feature", [("23.2.1", True), ("24.2", False), ("25.0.1", False)])
def test_os_truststore_is_enabled_without_upgrading_pip(arguments, monkeypatch, version, feature):
    import ensurepip
    monkeypatch.setattr(ensurepip, "version", lambda: version)
    result = installer.plan(arguments)
    assert result["certificateTrust"]["verificationRequired"] is True
    assert result["certificateTrust"]["osTruststore"] is True
    assert result["certificateTrust"]["truststoreFeatureFlag"] is feature
    command = result["commands"]["installWheel"]
    assert ("--use-feature=truststore" in command) is feature
    assert "--upgrade" not in command and "--trusted-host" not in command


def test_unsupported_bundled_pip_does_not_silently_fall_back(arguments, monkeypatch):
    import ensurepip
    monkeypatch.setattr(ensurepip, "version", lambda: "21.0")
    with pytest.raises(installer.InstallError, match="truststore"):
        installer.install(arguments)
    assert not arguments.install_dir.exists()


def test_explicit_ca_bundle_is_validated_hashed_and_snapshotted(arguments, monkeypatch):
    certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
    if not certificates:
        pytest.skip("No OS CA certificate is available for a public-certificate fixture")
    bundle = arguments.workspace.parent / "approved public CA.pem"
    bundle.write_text(ssl.DER_cert_to_PEM_cert(certificates[0]), encoding="ascii")
    arguments.ca_bundle = bundle
    fake_runtime(monkeypatch, arguments)
    arguments.apply = True
    result = installer.install(arguments)
    trust = result["certificateTrust"]
    assert trust["caBundleSha256"] == installer.digest(bundle)
    assert trust["verificationRequired"] and trust["osTruststore"]
    snapshot_path = arguments.install_dir / ".bootstrap" / "ca-bundle.pem"
    assert snapshot_path.read_bytes() == bundle.read_bytes()
    command = result["commands"]["installWheel"]
    assert command[command.index("--cert") + 1] == str(snapshot_path)


@pytest.mark.parametrize("content", [
    b"not PEM",
    b"-----BEGIN " + b"PRIVATE KEY-----\nPRIVATE MATERIAL\n-----END " + b"PRIVATE KEY-----",
    b"-----BEGIN CERTIFICATE-----\nINVALID\n-----END CERTIFICATE-----",
])
def test_invalid_or_private_ca_material_is_rejected_before_writes(arguments, content):
    bundle = arguments.workspace.parent / "not-approved.pem"
    bundle.write_bytes(content)
    arguments.ca_bundle = bundle
    with pytest.raises(installer.InstallError, match="PEM|public|private"):
        installer.install(arguments)
    assert not arguments.install_dir.exists()


def test_offline_requires_explicit_wheelhouse_and_disables_index(arguments):
    arguments.offline = True
    with pytest.raises(installer.InstallError, match="wheelhouse"):
        installer.install(arguments)
    arguments.wheelhouse = arguments.workspace.parent / "dependency wheels"
    arguments.wheelhouse.mkdir()
    result = installer.install(arguments)
    command = result["commands"]["installWheel"]
    assert "--no-index" in command and "--index-url" not in command
    assert command[command.index("--find-links") + 1] == str(arguments.install_dir / ".bootstrap" / "dependencies")
    assert result["dependencyPolicy"] == {
        "pipNoIndex": True, "binaryWheelsOnly": True, "indexUrl": None,
        "localWheelhouse": str(arguments.wheelhouse), "localWheelCount": 0,
        "localWheelsSnapshottedAndHashed": True, "localWheelhousePublisherVerified": False,
        "directUrlDependenciesAllowed": False,
        "inheritedPipConfiguration": False,
    }


def test_offline_wheelhouse_cannot_introduce_direct_url_dependencies(arguments):
    dependencies = arguments.workspace.parent / "dependency wheels"
    dependencies.mkdir()
    path = dependencies / "dependency-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("dependency-1.0.dist-info/METADATA",
                         "Name: dependency\nVersion: 1.0\nRequires-Dist: injected @ https://example.invalid/file.whl\n")
    arguments.offline, arguments.wheelhouse = True, dependencies
    with pytest.raises(installer.InstallError, match="Direct-URL"):
        installer.install(arguments)
    assert not arguments.install_dir.exists()


def test_wheelhouse_is_snapshotted_and_nonwheel_links_are_not_passed_to_pip(arguments, monkeypatch):
    dependencies = arguments.workspace.parent / "dependency wheels"
    dependencies.mkdir()
    dependency = make_wheel(dependencies)
    (dependencies / "index.html").write_text('<a href="https://example.invalid/file.whl">remote</a>', encoding="utf-8")
    arguments.offline, arguments.wheelhouse = True, dependencies
    fake_runtime(monkeypatch, arguments)
    arguments.apply = True
    result = installer.install(arguments)
    assert result["dependencyWheels"] == {dependency.name: installer.digest(dependency)}
    assert snapshot(arguments.install_dir / ".bootstrap" / "dependencies") == {dependency.name: dependency.read_bytes()}


def test_changed_wheel_between_preflight_and_copy_fails(arguments, monkeypatch):
    real_plan = installer.plan

    def changed(args):
        result = real_plan(args)
        args.wheel.write_bytes(b"changed")
        return result

    monkeypatch.setattr(installer, "plan", changed)
    arguments.apply = True
    with pytest.raises(installer.InstallError, match="changed after preview"):
        installer.install(arguments)
    assert not (arguments.install_dir / "installation.json").exists()


def test_main_reports_corrupt_wheel_cleanly(arguments, capsys):
    arguments.wheel.write_bytes(b"not a wheel")
    code = installer.main([
        "--wheel", str(arguments.wheel), "--sha256", installer.digest(arguments.wheel),
        "--workspace", str(arguments.workspace), "--install-dir", str(arguments.install_dir),
        "--plugin-dir", str(arguments.plugin_dir), "--apply",
    ])
    captured = capsys.readouterr()
    assert code == 1 and not captured.out
    assert json.loads(captured.err)["status"] == "failed"
    assert "Traceback" not in captured.err


PROVISION_DRY_RUN = """
import os
import runpy
import sys
sys.dont_write_bytecode = True
def deny_side_effects(event, args):
    if event in {
        "subprocess.Popen", "os.system", "socket.connect", "socket.bind",
        "socket.getaddrinfo", "socket.sendto", "os.mkdir", "os.rmdir",
        "os.remove", "os.rename", "os.link", "os.symlink", "os.truncate",
        "os.chmod", "os.utime",
    }:
        raise RuntimeError("Installed provision --dry-run attempted a side effect: " + event)
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        mode, flags = args[1], args[2]
        if (mode and any(char in mode for char in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            raise RuntimeError("Installed provision --dry-run attempted a file write")
sys.addaudithook(deny_side_effects)
sys.argv = ["mcp-kit", *sys.argv[1:]]
runpy.run_module("mcp_openapi_creator_kit.cli", run_name="__main__")
"""


@pytest.mark.skipif(not (os.environ.get("MCP_KIT_INSTALLER_WHEEL") and os.environ.get("MCP_KIT_INSTALLER_WHEELHOUSE")),
                    reason="Set explicit trusted product wheel and dependency wheelhouse to run the real offline installer")
def test_actual_supplied_wheel_offline(tmp_path):
    wheel = Path(os.environ["MCP_KIT_INSTALLER_WHEEL"])
    wheelhouse = Path(os.environ["MCP_KIT_INSTALLER_WHEELHOUSE"])
    customer = tmp_path / "Customer with spaces"
    customer.mkdir()
    (customer / "AGENTS.md").write_text("untrusted customer instructions", encoding="utf-8")
    bootstrap = tmp_path / "install-kit.py"
    bootstrap.write_bytes((ROOT / "install-kit.py").read_bytes())
    environment = os.environ.copy()
    environment.update(TEMP=str(tmp_path), TMP=str(tmp_path), TMPDIR=str(tmp_path),
                       PYTHONPATH=str(customer), PIP_INDEX_URL="https://unused.invalid/simple")
    before = snapshot(customer)
    outputs = []
    for suffix in ("first", "upgrade"):
        runtime = tmp_path / f"Runtime {suffix}"
        plugin = tmp_path / f"Plugin {suffix}"
        command = [
            sys.executable, "-I", str(bootstrap), "--wheel", str(wheel),
            "--sha256", installer.digest(wheel), "--workspace", str(customer),
            "--install-dir", str(runtime), "--plugin-dir", str(plugin),
            "--wheelhouse", str(wheelhouse), "--offline", "--apply",
        ]
        process = subprocess.run(command, cwd=tmp_path, env=environment,
                                 capture_output=True, text=True, encoding="utf-8")
        assert process.returncode == 0, process.stderr
        receipt = json.loads(process.stdout)
        assert receipt["status"] == "installed" and receipt["runtime"]["kit"]["source"] == "installed-package"
        assert not receipt["hostActivated"] and snapshot(customer) == before
        outputs.append((runtime, plugin, snapshot(plugin)))
        connection = json.loads((plugin / "connection.json").read_text("utf-8"))
        cli_prefix = connection["cliPrefix"]
        dry_run = subprocess.run([
            cli_prefix[0], "-I", "-B", "-c", PROVISION_DRY_RUN, *cli_prefix[4:],
            "provision", "--dry-run", "--account", "operator@example.invalid",
            "--tenant", "22222222-2222-4222-8222-222222222222",
            "--subscription", "11111111-1111-4111-8111-111111111111",
            "--resource-group", "rg-offline-installer-probe", "--location", "westeurope",
            "--apim-name", "apim-offline-installer-probe", "--publisher-name", "Offline installer probe",
            "--publisher-email", "operator@example.invalid", "--profile", "rest-consumption",
        ], cwd=customer, env=environment, capture_output=True, text=True, encoding="utf-8")
        assert dry_run.returncode == 0, dry_run.stderr
        marker = "[provision-gateway] Result"
        assert marker in dry_run.stdout
        provision = json.loads(dry_run.stdout.split(marker, 1)[1].strip())
        assert provision["schemaVersion"] == 1 and provision["status"] == "dry-run"
        assert provision["applied"] is False and provision["azureVerified"] is False
        assert provision["azdUsed"] is False and provision["resourceGroupMode"] == "existing-only"
        assert len(provision["creationId"]) == 64 and provision["ownershipTags"]
        assert "reviewToken" not in provision and "creationReceiptPath" not in provision
        assert provision["context"]["profile"] == "rest-consumption"
        assert snapshot(customer) == before
    assert snapshot(outputs[0][1]) == outputs[0][2]
    assert (outputs[0][0] / "installation.json").is_file()
    assert outputs[0][2][".mcp.json"] != outputs[1][2][".mcp.json"]
