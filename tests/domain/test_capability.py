"""Domain tests: cosine similarity math. Pure, no I/O."""

import pytest

from agent_api.domain.capability import (
    DEFAULT_MAX_RESULTS,
    MAX_MAX_RESULTS,
    Capability,
    cosine_similarity,
)


def test_identical_vectors_score_one():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_orthogonal_vectors_score_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_opposite_vectors_score_minus_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_zero_vector_scores_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_dimension_mismatch_raises():
    # m-16a: never silently zip-truncate mismatched vectors.
    with pytest.raises(ValueError, match="dimension mismatch"):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

# --- Result-trimming defaults / invariants on the domain model ---

def _cap(**overrides):
    kwargs = {
        "id": "x",
        "description": "x",
        "kind": "http",
        "connection": "c",
        "mode": "read",
    }
    kwargs.update(overrides)
    return Capability(**kwargs)


def test_trimming_defaults():
    cap = _cap()
    assert cap.list_paths == ()
    assert cap.keep == "first"
    assert cap.max_results == DEFAULT_MAX_RESULTS == 50
    assert MAX_MAX_RESULTS == 500


def test_list_paths_list_normalised_to_tuple():
    assert _cap(list_paths=["a.b"]).list_paths == ("a.b",)


@pytest.mark.parametrize("kwargs", [
    {"max_results": 0},
    {"max_results": 501},
    {"max_results": "100"},
    {"max_results": 50.5},
    {"max_results": True},
    {"keep": "newest"},
    {"keep": 1},
    {"list_paths": "a.b"},
    {"list_paths": [""]},
    {"list_paths": [1]},
    {"list_paths": None},
])
def test_invalid_trimming_settings_rejected(kwargs):
    with pytest.raises(ValueError):
        _cap(**kwargs)


# --- Token forwarding (ADR 0008) ---

def test_forward_user_token_defaults_false():
    assert _cap().forward_user_token is False


def test_forward_user_token_true_accepted():
    assert _cap(forward_user_token=True).forward_user_token is True


@pytest.mark.parametrize("value", ["true", "yes", 1, 0, None, ["true"]])
def test_forward_user_token_non_bool_rejected(value):
    with pytest.raises(ValueError, match="forward_user_token"):
        _cap(forward_user_token=value)
