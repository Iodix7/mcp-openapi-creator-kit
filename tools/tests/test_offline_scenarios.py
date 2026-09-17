import pytest

from offline_scenarios import BASE, SUB, SyntheticAzure, resource_ids


@pytest.mark.parametrize("args", [
    ["azd", "env", "get-values"],
    ["az", "deployment", "group", "create", "--subscription", SUB],
    ["az", "rest", "--subscription", SUB, "--method", "DELETE", "--uri", BASE],
    ["az", "rest", "--subscription", SUB, "--method", "GET", "--uri", BASE + "/unknown?api-version=2024-06-01-preview"],
    ["az", "rest", "--subscription", SUB, "--method", "GET", "--uri", BASE + "?api-version=2099-01-01"],
    ["az", "rest", "--subscription", SUB, "--method", "GET", "--uri", "https://evil.example.invalid" + BASE],
    ["az", "keyvault", "secret", "show", "--subscription", SUB],
])
def test_unmodeled_transport_fails_closed(tmp_path, args):
    with pytest.raises(AssertionError):
        SyntheticAzure(tmp_path).run(args)


def test_fixture_oracle_is_independent_and_selected():
    ids = resource_ids()
    assert len([rid for rid in ids if "/tools/" in rid]) == 6
    assert f"{BASE}/apis/acme-customer-care-acme" in ids
    assert not any("/namedValues/" in rid or "/sample" in rid or "/other" in rid for rid in ids)
    assert set(resource_ids(True)) - set(ids) == {
        f"{BASE}/tags/acme-external",
        f"{BASE}/apis/acme-customer-care-acme/tags/acme-external",
        f"{BASE}/namedValues/acme-key",
    }
