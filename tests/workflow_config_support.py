"""Shared navigation and parsing helpers for GitHub Actions workflow contract tests."""

from collections.abc import Callable
from pathlib import Path
from typing import Final, TextIO

import yaml


type WorkflowValue = (
    str | list[WorkflowValue] | dict[str, WorkflowValue] | None
)
type WorkflowLoader = Callable[
    [TextIO, type[yaml.BaseLoader]], WorkflowValue
]


def _typed_workflow_loader(loader: WorkflowLoader) -> WorkflowLoader:
    return loader


_YAML_LOAD: Final = _typed_workflow_loader(yaml.load)
REPOSITORY_ROOT: Final = Path(__file__).resolve().parent.parent
SCRIPT_DIR: Final = REPOSITORY_ROOT / ".github" / "scripts"
WORKFLOW_DIR: Final = REPOSITORY_ROOT / ".github" / "workflows"
WORKFLOW_NAMES: Final = ("main.yml", "test.yml")


def load_workflow(name: str) -> dict[str, WorkflowValue]:
    with (WORKFLOW_DIR / name).open(encoding="utf-8") as stream:
        workflow = _YAML_LOAD(stream, yaml.BaseLoader)
    assert isinstance(workflow, dict)
    return workflow


def workflow_job(name: str) -> dict[str, WorkflowValue]:
    workflow = load_workflow(name)
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["calculate-and-send"]
    assert isinstance(job, dict)
    return job


def workflow_steps(name: str) -> list[dict[str, WorkflowValue]]:
    job = workflow_job(name)
    steps = job["steps"]
    assert isinstance(steps, list)
    typed_steps: list[dict[str, WorkflowValue]] = []
    for step in steps:
        assert isinstance(step, dict)
        typed_steps.append(step)
    return typed_steps


def named_step(name: str, step_name: str) -> dict[str, WorkflowValue]:
    for step in workflow_steps(name):
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"{step_name} step is missing")


def step_environment(name: str, step_name: str) -> dict[str, WorkflowValue]:
    environment = named_step(name, step_name)["env"]
    assert isinstance(environment, dict)
    return environment


def action_script(name: str, step_name: str) -> str:
    action_inputs = named_step(name, step_name)["with"]
    assert isinstance(action_inputs, dict)
    script = action_inputs["script"]
    assert isinstance(script, str)
    return script


def run_environment(name: str) -> dict[str, WorkflowValue]:
    return step_environment(name, "Run script")
