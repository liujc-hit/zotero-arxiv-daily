"""GitHub Actions contracts for the shared sent-DOI state loader."""

from typing import Final

import pytest

from .workflow_config_support import (
    REPOSITORY_ROOT,
    WORKFLOW_NAMES,
    action_script,
    named_step,
    workflow_steps,
)


_LOAD_STEP: Final = "Load encrypted sent DOI state"
_LOAD_MODULE: Final = ".github/scripts/load-sent-doi-state.cjs"
_LOAD_WRAPPER: Final = (
    'const loadSentDoiState = require("./.github/scripts/load-sent-doi-state.cjs");\n'
    "await loadSentDoiState({ github, context, core });\n"
)


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_load_step_is_an_exact_thin_commonjs_wrapper(workflow_name: str) -> None:
    # Given the parsed loader action.
    step = named_step(workflow_name, _LOAD_STEP)

    # When its executable boundary is inspected, then only the shared module runs.
    assert step["id"] == "load-sent-doi-state"
    assert step["uses"] == "actions/github-script@v8"
    assert step["with"] == {
        "retries": "0",
        "script": _LOAD_WRAPPER,
    }
    assert action_script(workflow_name, _LOAD_STEP).splitlines() == [
        'const loadSentDoiState = require("./.github/scripts/load-sent-doi-state.cjs");',
        "await loadSentDoiState({ github, context, core });",
    ]


@pytest.mark.parametrize("workflow_name", WORKFLOW_NAMES)
def test_load_module_exists_after_checkout_before_action_use(
    workflow_name: str,
) -> None:
    # Given a local module required by github-script.
    step_names = [step.get("name") for step in workflow_steps(workflow_name)]

    # When checkout order and the resolved repository path are inspected.
    assert (REPOSITORY_ROOT / _LOAD_MODULE).is_file()
    assert step_names.index("Checkout") < step_names.index(_LOAD_STEP)
