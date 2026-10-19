"""Offline deployment contracts for optional encrypted compute snapshots."""

import re
import shlex
from typing import Final

import pytest
from hydra.core.override_parser.overrides_parser import OverridesParser
from omegaconf import OmegaConf

from .workflow_config_support import (
    REPOSITORY_ROOT,
    WORKFLOW_NAMES,
    action_script,
    named_step,
    run_environment,
    step_environment,
    workflow_job,
    workflow_steps,
)

_TRUSTED: Final = (
    "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
    " && (github.event_name == 'schedule' || github.event_name == 'workflow_dispatch')"
)
_CACHE_STEPS: Final = (
    "Prepare compute cache",
    "Restore compute cache",
    "Before save compute cache",
    "Save compute cache",
    "After save compute cache",
)
_PHASES: Final = {
    "Prepare compute cache": "prepare",
    "Before save compute cache": "before-save",
    "After save compute cache": "after-save",
}
_PATH: Final = ".cache/zad-compute/*.enc"


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_cache_secret_is_available_only_in_trusted_cache_steps(workflow_name: str) -> None:
    # Given the workflow's scoped cache environments.
    expected_steps = {*_PHASES, "Run script"}
    steps = workflow_steps(workflow_name)

    # When cache-bearing environments are inspected.
    cache_steps = [step for step in steps if "COMPUTE_CACHE_KEY" in (step.get("env") or {})]

    # Then the separate key never reaches an unguarded environment.
    cache_names: set[str] = set()
    for step in cache_steps:
        name = step["name"]
        assert isinstance(name, str)
        cache_names.add(name)
    assert cache_names == expected_steps
    for step in cache_steps:
        environment = step_environment(workflow_name, str(step["name"]))
        assert environment["COMPUTE_CACHE_KEY"] == (
            "${{ " + _TRUSTED + " && secrets.COMPUTE_CACHE_KEY || '' }}"
        )
        preparation_gate = "steps.prepare-compute-cache.outputs.enabled == 'true' && " if step["name"] == "Run script" else ""
        assert environment["COMPUTE_CACHE_ENABLED"] == (
            "${{ " + _TRUSTED + " && " + preparation_gate
            + "(vars.COMPUTE_CACHE_ENABLED || 'false') || 'false' }}"
        )
        assert environment["COMPUTE_CACHE_MODEL_REVISION"] == "${{ vars.COMPUTE_CACHE_MODEL_REVISION || '' }}"
        assert environment["COMPUTE_CACHE_BUDGET_BYTES"] == "${{ vars.COMPUTE_CACHE_BUDGET_BYTES || '8000000000' }}"
    assert "steps.prepare-compute-cache.outputs.enabled == 'true'" in str(run_environment(workflow_name)["COMPUTE_CACHE_ENABLED"])
    assert {name for name in run_environment(workflow_name) if name.startswith("COMPUTE_CACHE_")} == {
        "COMPUTE_CACHE_ENABLED", "COMPUTE_CACHE_KEY",
        "COMPUTE_CACHE_MODEL_REVISION", "COMPUTE_CACHE_BUDGET_BYTES",
    }


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_optional_cache_lifecycle_preserves_delivery_state_order(workflow_name: str) -> None:
    # Given steps sharing the existing delivery-state lock.
    steps = workflow_steps(workflow_name)
    names = [step["name"] for step in steps]

    # When execution order and permissions are inspected.
    permissions = workflow_job(workflow_name)["permissions"]

    # Then preparation precedes restore and publication cannot block state persistence.
    assert names[:3] == ["Checkout", "Prepare compute cache", "Restore compute cache"]
    ordered = ["Load encrypted sent DOI state", "Run script", "Persist encrypted sent DOI state", *_CACHE_STEPS[2:]]
    assert [name for name in names if name in ordered] == ordered
    assert permissions == {"contents": "write", "actions": "write"}
    for name in _CACHE_STEPS:
        step = named_step(workflow_name, name)
        assert step["continue-on-error"] == "true"
        assert _TRUSTED in str(step["if"])


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_cache_archives_only_ciphertext_with_identical_restore_save_paths(workflow_name: str) -> None:
    # Given official split restore/save actions.
    restore = named_step(workflow_name, "Restore compute cache")
    save = named_step(workflow_name, "Save compute cache")

    # When archive inputs are read.
    restore_inputs, save_inputs = restore["with"], save["with"]
    assert isinstance(restore_inputs, dict)
    assert isinstance(save_inputs, dict)

    # Then no state, status, model name, source identity or plaintext path is uploaded.
    assert restore["uses"] == "actions/cache/restore@v4"
    assert save["uses"] == "actions/cache/save@v4"
    assert restore_inputs == {
        "path": _PATH,
        "key": "${{ steps.prepare-compute-cache.outputs.restore-key }}",
        "restore-keys": "${{ steps.prepare-compute-cache.outputs.restore-prefix }}",
    }
    assert save_inputs == {"path": _PATH, "key": "${{ steps.before-save-compute-cache.outputs.save-key }}"}
    assert "steps.prepare-compute-cache.outputs.enabled == 'true'" in str(restore["if"])
    assert "success()" in str(save["if"])
    assert "steps.before-save-compute-cache.outputs.save-allowed == 'true'" in str(save["if"])


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_cache_conditions_cannot_bypass_trust_or_flush_outputs(workflow_name: str) -> None:
    # Given the trusted default-branch gate and helper output contracts.
    prepared = "steps.prepare-compute-cache.outputs.enabled == 'true'"
    allowed = "steps.before-save-compute-cache.outputs.save-allowed == 'true'"
    conditions = {
        "Prepare compute cache": _TRUSTED,
        "Restore compute cache": f"{_TRUSTED} && {prepared}",
        "Before save compute cache": f"success() && {_TRUSTED} && {prepared}",
        "Save compute cache": f"success() && {_TRUSTED} && {allowed}",
        "After save compute cache": f"success() && {_TRUSTED} && {allowed} && steps.save-compute-cache.outcome == 'success'",
    }

    # When all lifecycle predicates are collected.
    actual = {name: named_step(workflow_name, name)["if"] for name in conditions}

    # Then no fallback can enable cache access on another ref/event or a failed flush.
    assert actual == {name: "${{ " + condition + " }}" for name, condition in conditions.items()}


def test_workflows_share_identical_cache_lifecycle_contracts() -> None:
    # Given the production and debug workflows.
    main = [named_step("main.yml", name) for name in _CACHE_STEPS]

    # When their optional cache lifecycle is compared.
    manual = [named_step("test.yml", name) for name in _CACHE_STEPS]

    # Then both use the same trust, namespace, budget and transport wiring.
    assert main == manual


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
@pytest.mark.parametrize("name,phase", tuple(_PHASES.items()))
def test_cache_helper_uses_frozen_commonjs_phase_contract(workflow_name: str, name: str, phase: str) -> None:
    # Given a lifecycle phase wrapper.
    step = named_step(workflow_name, name)

    # When the executable boundary is inspected.
    script = action_script(workflow_name, name)

    # Then all phases call the shared dependency-free helper with the same context.
    assert step["uses"] == "actions/github-script@v8"
    assert step_environment(workflow_name, name)["COMPUTE_CACHE_PHASE"] == phase
    assert script == (
        'const computeCache = require("./.github/scripts/compute-cache.cjs");\n'
        "await computeCache({ github, context, core, env: process.env });\n"
    )


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
@pytest.mark.parametrize("enabled", ("false", "true"))
def test_cache_cli_overrides_parse_typed_controls_without_replacing_custom_overlay(
    workflow_name: str, enabled: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given workflow controls distinct from a private full overlay's shard/TTL settings.
    command = named_step(workflow_name, "Run script")["run"]
    assert isinstance(command, str)
    monkeypatch.setenv("COMPUTE_CACHE_ENABLED", enabled)
    monkeypatch.setenv("COMPUTE_CACHE_KEY", "")
    monkeypatch.setenv("COMPUTE_CACHE_MODEL_REVISION", "owner-epoch-2")
    monkeypatch.setenv("COMPUTE_CACHE_BUDGET_BYTES", "8000000000")
    overlay = OmegaConf.create({"compute_cache": {"shard_bytes": 1048576, "ttl_days": 3}})

    # When the actual shell arguments are parsed by Hydra, without running the app.
    arguments = shlex.split(command.replace("\\\n", " "))
    overrides = [argument for argument in arguments if argument.startswith("++compute_cache.")]
    parsed = OverridesParser.create().parse_overrides(overrides)
    values = OmegaConf.from_dotlist([f"{item.key_or_group}={item.value()}" for item in parsed])
    merged = OmegaConf.merge(overlay, values)

    # Then five explicit controls are typed, and user shard/TTL values survive.
    assert len(overrides) == 5
    assert len([argument for argument in arguments if argument.startswith("++")]) == 8
    assert OmegaConf.to_container(merged, resolve=True) == {"compute_cache": {
        "enabled": enabled == "true", "key": "", "model_revision": "owner-epoch-2",
        "budget_bytes": 8000000000, "path": ".cache/zad-compute",
        "shard_bytes": 1048576, "ttl_days": 3,
    }}
    assert re.search(r'printf "%b\\n" "\$CUSTOM_CONFIG" > config/custom.yaml', command)


def test_cache_defaults_are_disabled_and_merge_into_older_overlays() -> None:
    # Given public defaults only, never a user's private configuration.
    base = OmegaConf.load(REPOSITORY_ROOT / "config/base.yaml")

    # When an older overlay without compute-cache keys is composed.
    merged = OmegaConf.merge(base, {"executor": {"debug": True}})

    # Then enabling requires explicit credentials and a revision namespace.
    merged_values = OmegaConf.to_container(merged)
    assert isinstance(merged_values, dict)
    assert merged_values["compute_cache"] == {
        "enabled": False, "key": None, "path": ".cache/zad-compute",
        "budget_bytes": 8000000000, "shard_bytes": 16777216,
        "ttl_days": 7, "model_revision": None,
    }
    ignores = (REPOSITORY_ROOT / ".gitignore").read_text().splitlines()
    assert ".cache/zad-compute/" in ignores
    assert ".cache/zad-compute-status.json" in ignores
    assert ".cache/" not in ignores
