"""GitHub Actions contracts for the encrypted sent-DOI state lifecycle."""

import re
from typing import Final

import pytest

from .workflow_config_support import (
    SCRIPT_DIR,
    WORKFLOW_DIR,
    WORKFLOW_NAMES,
    WorkflowValue,
    action_script,
    load_workflow,
    named_step,
    workflow_job,
    workflow_steps,
)


_STATE_BRANCH: Final = "sent-doi-state-v1"
_STATE_PATH: Final = ".state/sent-dois.fernet"
_STATE_LIFECYCLE_STEPS: Final = (
    "Load encrypted sent DOI state",
    "Run script",
    "Persist encrypted sent DOI state",
)
_STATE_ENABLED_BINDING: Final = "${{ vars.SENT_DOI_STATE_ENABLED || 'false' }}"
_STATE_KEY_BINDING: Final = "${{ secrets.SENT_DOI_STATE_KEY }}"
_STATE_KEY_PRESENT_BINDING: Final = "${{ secrets.SENT_DOI_STATE_KEY != '' }}"
_PERSIST_WRAPPER: Final = (
    'const persistSentDoiState = require("./.github/scripts/persist-sent-doi-state.cjs");\n'
    "await persistSentDoiState({ github, context, core });\n"
)
_STATE_MODULE_NAMES: Final = (
    "load-sent-doi-state.cjs",
    "persist-sent-doi-state.cjs",
)


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_state_job_has_scoped_write_permission_and_serialized_updates(
    workflow_name: str,
) -> None:
    # Given a workflow that reads and writes one shared state branch.
    workflow = load_workflow(workflow_name)
    job = workflow_job(workflow_name)

    # When its authority and concurrency contract are inspected.
    permissions = job.get("permissions")
    concurrency = workflow.get("concurrency")

    # Then only the job receives write authority and both workflows share one lock.
    assert "permissions" not in workflow
    assert permissions == {"contents": "write"}
    assert concurrency == {
        "group": _STATE_BRANCH,
        "cancel-in-progress": "false",
    }


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_state_controls_are_bound_only_at_required_steps(
    workflow_name: str,
) -> None:
    # Given every step-level environment in a state-aware workflow.
    key_bindings: dict[str, WorkflowValue] = {}
    key_present_bindings: dict[str, WorkflowValue] = {}
    enabled_bindings: dict[str, WorkflowValue] = {}
    for step in workflow_steps(workflow_name):
        step_name = step.get("name")
        environment = step.get("env")
        assert isinstance(step_name, str)
        if not isinstance(environment, dict):
            continue
        if "SENT_DOI_STATE_KEY" in environment:
            key_bindings[step_name] = environment["SENT_DOI_STATE_KEY"]
        if "SENT_DOI_STATE_KEY_PRESENT" in environment:
            key_present_bindings[step_name] = environment["SENT_DOI_STATE_KEY_PRESENT"]
        if "SENT_DOI_STATE_ENABLED" in environment:
            enabled_bindings[step_name] = environment["SENT_DOI_STATE_ENABLED"]

    # When state-specific bindings are collected, then raw key access is isolated.
    assert named_step(workflow_name, "Load encrypted sent DOI state")["env"] == {
        "SENT_DOI_STATE_ENABLED": _STATE_ENABLED_BINDING,
        "SENT_DOI_STATE_KEY_PRESENT": _STATE_KEY_PRESENT_BINDING,
    }
    assert key_bindings == {
        "Run script": _STATE_KEY_BINDING,
    }
    assert key_present_bindings == {
        "Load encrypted sent DOI state": _STATE_KEY_PRESENT_BINDING,
    }
    assert enabled_bindings == {
        step_name: _STATE_ENABLED_BINDING for step_name in _STATE_LIFECYCLE_STEPS
    }


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_workflow_does_not_manually_expose_a_github_token(workflow_name: str) -> None:
    # Given the complete workflow source and action inputs.
    source = (WORKFLOW_DIR / workflow_name).read_text(encoding="utf-8")

    # When token wiring is inspected, then github-script must use its default auth.
    assert "github.token" not in source
    assert "secrets.GITHUB_TOKEN" not in source
    assert "GH_TOKEN" not in source
    assert "GITHUB_TOKEN" not in source
    for step in workflow_steps(workflow_name):
        action_inputs = step.get("with")
        if isinstance(action_inputs, dict):
            assert "github-token" not in action_inputs


def test_workflows_share_identical_state_lifecycle_order_and_actions() -> None:
    # Given production and manual workflows with different application settings.
    lifecycle_names: dict[str, list[str]] = {}
    for workflow_name in WORKFLOW_NAMES:
        lifecycle_names[workflow_name] = [
            step_name
            for step in workflow_steps(workflow_name)
            if isinstance((step_name := step.get("name")), str)
            and step_name in _STATE_LIFECYCLE_STEPS
        ]

    # When lifecycle steps are compared, then ordering and state actions are equal.
    assert lifecycle_names == {
        workflow_name: list(_STATE_LIFECYCLE_STEPS)
        for workflow_name in WORKFLOW_NAMES
    }
    for step_name in (
        "Load encrypted sent DOI state",
        "Persist encrypted sent DOI state",
    ):
        assert named_step("main.yml", step_name) == named_step("test.yml", step_name)


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_app_uses_only_the_local_encrypted_state_path(workflow_name: str) -> None:
    # Given the application step after state loading.
    run_step = named_step(workflow_name, "Run script")
    command = run_step["run"]
    assert isinstance(command, str)

    # When Hydra overrides are inspected, then state is env-gated and local only.
    assert "'++sent_doi_state.enabled=${oc.decode:${oc.env:SENT_DOI_STATE_ENABLED}}'" in command
    assert f"'++sent_doi_state.path={_STATE_PATH}'" in command
    assert "'++sent_doi_state.key=${oc.env:SENT_DOI_STATE_KEY}'" in command
    assert "uv run --locked src/zotero_arxiv_daily/main.py" in command
    assert command.index("printf") < command.index("uv run --locked")


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_state_persist_is_an_exact_thin_commonjs_wrapper(
    workflow_name: str,
) -> None:
    # Given the parsed post-success publisher action.
    step = named_step(workflow_name, "Persist encrypted sent DOI state")

    # When its executable boundary is inspected, then only the shared module runs.
    assert step["if"] == "${{ success() }}"
    assert step["uses"] == "actions/github-script@v8"
    assert step["with"] == {
        "retries": "0",
        "script": _PERSIST_WRAPPER,
    }
    assert action_script(
        workflow_name,
        "Persist encrypted sent DOI state",
    ).splitlines() == [
        'const persistSentDoiState = require("./.github/scripts/persist-sent-doi-state.cjs");',
        "await persistSentDoiState({ github, context, core });",
    ]


def test_state_commonjs_module_paths_exist() -> None:
    # Given the exact local paths consumed by both github-script wrappers.
    module_paths = tuple(SCRIPT_DIR / name for name in _STATE_MODULE_NAMES)

    # When repository files are resolved, then both executable modules are present.
    assert all(module_path.is_file() for module_path in module_paths)


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_state_lifecycle_has_no_unsafe_fallback_or_logging(workflow_name: str) -> None:
    # Given the checked-in workflow and shared state modules.
    source = (WORKFLOW_DIR / workflow_name).read_text(encoding="utf-8")
    module_sources = tuple(
        (SCRIPT_DIR / name).read_text(encoding="utf-8")
        for name in _STATE_MODULE_NAMES
    )

    # When unsafe surfaces are inspected, then every failure remains terminal.
    for forbidden in (
        "continue-on-error",
        "actions/cache",
        "actions/upload-artifact",
        "api.github.com",
        "set -x",
        "tee ",
        "|| true",
    ):
        assert forbidden not in source
    assert named_step(workflow_name, "Checkout")["uses"] == "actions/checkout@v6"
    assert named_step(workflow_name, "Checkout").get("with") == {
        "persist-credentials": "false"
    }
    for module_source in module_sources:
        assert "JSON.stringify" not in module_source
        assert "error.message" not in module_source
        assert re.search(
            r"\b(?:console\.|core\.(?:debug|info|notice|warning|error))",
            module_source,
        ) is None
        assert re.search(r"\bgit\s", module_source) is None
        assert re.search(r"\bSENT_DOI_STATE_KEY\b", module_source) is None
