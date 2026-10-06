"""Unit tests for default-deny risk classification."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from overlay import risk as risk_policy
from overlay.domain import ExecutionPolicy, Owner, RiskClass

pytestmark = pytest.mark.unit


def _step(action, risk=None, owner=None):
    return SimpleNamespace(action=action, risk=risk, owner=owner)


def test_read_only_check_is_agent_runnable():
    risk, owner, policy, _ = risk_policy.classify(_step("check"))
    assert risk is RiskClass.R0
    assert owner is Owner.AI
    assert policy is not ExecutionPolicy.MANUAL_ONLY


def test_service_restart_needs_its_own_approval():
    risk, owner, policy, _ = risk_policy.classify(_step("restart_service"))
    assert risk is RiskClass.R2
    assert owner is Owner.AI
    assert policy is ExecutionPolicy.APPROVE_STEP


def test_raw_command_is_human_only_regardless_of_declared_risk():
    # An upload cannot widen an arbitrary command into an agent action.
    risk, owner, policy, _ = risk_policy.classify(_step("run_command", risk="R0"))
    assert owner is Owner.HUMAN
    assert policy is ExecutionPolicy.MANUAL_ONLY


def test_unknown_action_defaults_to_rx_human_only():
    risk, owner, policy, _ = risk_policy.classify(_step("made_up_action"))
    assert risk is RiskClass.RX
    assert owner is Owner.HUMAN
    assert policy is ExecutionPolicy.MANUAL_ONLY


@pytest.mark.parametrize("action", ["reboot", "delete_profile", "manual"])
def test_destructive_actions_stay_human(action):
    _, owner, policy, _ = risk_policy.classify(_step(action))
    assert owner is Owner.HUMAN
    assert policy is ExecutionPolicy.MANUAL_ONLY


def test_explicit_human_owner_pins_step_to_human():
    _, owner, policy, _ = risk_policy.classify(_step("check", owner="human"))
    assert owner is Owner.HUMAN
    assert policy is ExecutionPolicy.MANUAL_ONLY
