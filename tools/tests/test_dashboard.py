import concurrent.futures
import urllib.error
import urllib.request

import pytest

from mcp_openapi_creator_kit.dashboard import DashboardHost
from mcp_openapi_creator_kit.workspace import WorkspaceError, WorkspaceReader


def fetch(url: str, host: str | None = None):
    request = urllib.request.Request(url)
    if host is not None:
        request.add_header("Host", host)
    return urllib.request.urlopen(request, timeout=5)


def test_dashboard_is_tokenized_loopback_only_and_no_store():
    dashboard = DashboardHost()
    try:
        info = dashboard.publish("<!doctype html><title>catalog</title>")
        assert info.url.startswith("http://127.0.0.1:")
        with fetch(info.url) as response:
            assert response.read().decode() == "<!doctype html><title>catalog</title>"
            assert response.headers["Cache-Control"] == "no-store, max-age=0"
            assert "default-src 'none'" in response.headers["Content-Security-Policy"]

        bad_token = info.url.rsplit("/", 1)[0] + "/wrong"
        with pytest.raises(urllib.error.HTTPError) as error:
            fetch(bad_token)
        assert error.value.code == 404
        assert error.value.headers["Cache-Control"] == "no-store, max-age=0"
        with pytest.raises(urllib.error.HTTPError) as error:
            fetch(info.url + "/../catalog")
        assert error.value.code == 404

        with pytest.raises(urllib.error.HTTPError) as error:
            fetch(info.url, "example.com")
        assert error.value.code == 421
    finally:
        dashboard.close()


def test_dashboard_publish_is_atomic_under_concurrency():
    dashboard = DashboardHost()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(
                lambda index: dashboard.publish(f"<html>{index}</html>"),
                range(32),
            ))
        assert len({item.url for item in results}) == 1
        assert dashboard.info().generation == 32
        with fetch(dashboard.info().url) as response:
            body = response.read().decode()
        assert body.startswith("<html>") and body.endswith("</html>")
    finally:
        dashboard.close()


def test_workspace_rejects_catalog_symlink_outside_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "openapi.yaml").write_text("openapi: 3.0.3\n", encoding="utf-8")
    root = tmp_path / "workspace"
    (root / "apis").mkdir(parents=True)
    (root / "catalog").mkdir()
    (root / "catalog" / "metadata.yaml").write_text("{}\n", encoding="utf-8")
    (root / "catalog" / "template.html").write_text(
        "__CATALOG_DATA__", encoding="utf-8")
    try:
        (root / "apis" / "escaped").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available")

    with pytest.raises(WorkspaceError):
        WorkspaceReader(root).catalog()
