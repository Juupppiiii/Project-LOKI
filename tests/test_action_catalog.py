"""Unit tests for the versioned action catalogue."""

from __future__ import annotations

import pytest

from overlay import action_catalog

pytestmark = pytest.mark.unit


def test_known_action_resolves_to_exact_version():
    definition = action_catalog.resolve("restart_service")
    assert definition.reference == "restart_service@1.0.0"
    assert definition.connector == "simulation"
    assert definition.operation == "restart_service"
    assert definition.ai_eligible is True


def test_unknown_action_fails_closed():
    with pytest.raises(action_catalog.UnknownAction):
        action_catalog.resolve("definitely_not_a_real_action")


def test_wrong_version_is_rejected():
    with pytest.raises(action_catalog.UnknownAction):
        action_catalog.resolve("restart_service", "9.9.9")


@pytest.mark.parametrize("action", ["run_command", "reboot", "delete_profile", "manual"])
def test_destructive_and_arbitrary_actions_are_human_only(action):
    definition = action_catalog.resolve(action)
    assert definition.ai_eligible is False
    assert definition.connector == "human"


def test_catalogue_version_is_stable_and_deterministic():
    first = action_catalog.catalogue_version()
    second = action_catalog.catalogue_version()
    assert first == second
    assert first.startswith("sha256:")


def test_binding_exposes_enforcement_fields():
    binding = action_catalog.resolve("restart_service").binding()
    assert binding["reference"] == "restart_service@1.0.0"
    assert binding["connector"] == "simulation"
    assert binding["operation"] == "restart_service"
    assert binding["ai_eligible"] is True
    assert "service_name" in binding["required_parameters"]


def test_required_parameters_are_a_subset_of_parameter_names():
    for definition in action_catalog.catalogue():
        assert set(definition.required_parameters).issubset(
            set(definition.parameter_names)
        )
