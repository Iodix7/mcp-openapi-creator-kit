import pytest

from mcp_openapi_creator_kit.scenario import (
    apply_spec_sync, check_spec, functional_fit, inventory, plan_spec_sync, require_consistent_spec,
)

HEADER = "| Requested outcome | Tool or gap | Fit |\n|---|---|---|\n"
ITALIAN_HEADER = "| Risultato richiesto | Strumento | Fit |\n|---|---|---|\n"


def test_missing_mapping_is_visible_not_a_semantic_verdict(mcp_workspace):
    result = functional_fit("Legacy scenario", inventory(mcp_workspace, "fixture"))
    assert result["status"] == "not-recorded"
    assert "No semantic verdict" in result["notice"]


@pytest.mark.parametrize("outcome,state", [
    ("Check service coverage, not commercial eligibility", "missing"),
    ("Create support request, not reschedule appointment", "proposed"),
    ("Persist an accepted request, not simulate a write", "partial"),
])
@pytest.mark.parametrize("header", [HEADER, ITALIAN_HEADER])
def test_explicit_functional_gaps_block_prepare(mcp_workspace, outcome, state, header):
    records = inventory(mcp_workspace, "fixture")
    path = mcp_workspace / "docs" / "fixture" / "spec.md"
    path.parent.mkdir(parents=True)
    path.write_text(header + f"| {outcome} | missing capability | {state} |\n", encoding="utf-8")
    apply_spec_sync(mcp_workspace, plan_spec_sync(mcp_workspace, "fixture", records))
    result = check_spec(mcp_workspace, "fixture", records)
    assert result.status == "mismatch"
    assert any("Unresolved functional gap" in i.message for i in result.issues)
    with pytest.raises(ValueError, match="Unresolved functional gap"):
        require_consistent_spec(mcp_workspace, "fixture")


def test_mapping_does_not_pretend_to_understand_business_semantics(mcp_workspace):
    text = HEADER + "| Inspect `customerId` | `get-customer-context` | covered |\n"
    result = functional_fit(text, inventory(mcp_workspace, "fixture"))
    assert result["status"] == "recorded-not-verified"
    assert not result["issues"]
    incorrect_prose = text.replace("Inspect `customerId`", "Persist a new customer")
    assert functional_fit(incorrect_prose, inventory(mcp_workspace, "fixture"))["status"] == "recorded-not-verified"


@pytest.mark.parametrize("tool", ["get-customer-context", "`nonexistent-tool`", "`create-reschedule-request`"])
@pytest.mark.parametrize("header", [HEADER, ITALIAN_HEADER])
def test_covered_requires_exact_selected_tool_reference(mcp_workspace, tool, header):
    assert functional_fit(header + f"| Read context | {tool} | covered |\n",
                          inventory(mcp_workspace, "fixture"))["issues"]


def test_scoped_out_requires_recorded_user_decision_not_inferred_consent(mcp_workspace):
    records = inventory(mcp_workspace, "fixture")
    assert functional_fit(HEADER + "| Persist | external backend | scoped-out |\n", records)["issues"]
    text = ("| Requested outcome | Tool or gap | Fit | Decision evidence |\n"
            "|---|---|---|---|\n| Persist | external backend | scoped-out | User chose mock-only scope |\n")
    assert functional_fit(text, records)["status"] == "recorded-not-verified"
    assert functional_fit(text.replace("User chose mock-only scope", "pending"), records)["issues"]
    assert functional_fit(HEADER, records)["issues"]


@pytest.mark.parametrize("tool_header", ["Strumento", "Strumento o gap"])
def test_native_italian_mapping_has_same_structural_checks(mcp_workspace, tool_header):
    records = inventory(mcp_workspace, "fixture")
    row = "| Consultare il contesto cliente | `get-customer-context` | covered |\n"
    localized = ITALIAN_HEADER.replace("Strumento", tool_header) + row
    assert functional_fit(localized, records) == functional_fit(HEADER + row, records)
    assert functional_fit(localized, records)["status"] == "recorded-not-verified"
    path = mcp_workspace / "docs" / "fixture" / "spec.md"
    path.parent.mkdir(parents=True)
    path.write_text(localized, encoding="utf-8")
    apply_spec_sync(mcp_workspace, plan_spec_sync(mcp_workspace, "fixture", records))
    assert check_spec(mcp_workspace, "fixture", records).status == "consistent"


def test_italian_exclusion_requires_actual_decision_evidence(mcp_workspace):
    records = inventory(mcp_workspace, "fixture")
    text = ("| Risultato richiesto | Strumento o gap | Fit | Evidenza decisione |\n"
            "|---|---|---|---|\n"
            "| Persistenza | backend esterno | scoped-out | User chose mock-only scope |\n")
    assert functional_fit(text, records)["status"] == "recorded-not-verified"
    assert functional_fit(text.replace("User chose mock-only scope", ""), records)["issues"]


def test_duplicate_localized_headers_cannot_hide_a_gap(mcp_workspace):
    text = ("| Requested outcome | Risultato richiesto | Tool or gap | Fit |\n"
            "|---|---|---|---|\n"
            "| Persistence | Context | `get-customer-context` | covered |\n")
    assert functional_fit(text, inventory(mcp_workspace, "fixture"))["issues"]
