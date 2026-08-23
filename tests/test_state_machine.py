from __future__ import annotations

from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from metric_pulse.models import TaskStatus
from metric_pulse.state_machine import ALLOWED_TRANSITIONS, InvalidTransition, ensure_transition


def test_known_happy_path_transitions() -> None:
    path = [
        TaskStatus.DRAFT,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.PAUSING,
        TaskStatus.PAUSED,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.SUCCEEDED,
        TaskStatus.DELETED,
    ]
    for source, target in pairwise(path):
        ensure_transition(source, target)


@given(st.sampled_from(list(TaskStatus)), st.sampled_from(list(TaskStatus)))
def test_transition_function_matches_declared_graph(source: TaskStatus, target: TaskStatus) -> None:
    if target in ALLOWED_TRANSITIONS[source]:
        ensure_transition(source, target)
    else:
        with pytest.raises(InvalidTransition):
            ensure_transition(source, target)


def test_deleted_is_terminal() -> None:
    assert not ALLOWED_TRANSITIONS[TaskStatus.DELETED]
