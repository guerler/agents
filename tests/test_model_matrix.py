"""Each eval model's key variable comes from the agent's registry, never a copy kept here.

The variable names moved when Olit adopted pi's own provider definitions (GEMINI_KEY became
GEMINI_API_KEY); a matrix that named them itself went on skipping or failing with the old ones.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "evals"))

from lib import harness  # noqa: E402

MATRIX = json.loads((pathlib.Path(__file__).resolve().parents[1] / "evals/models.json").read_text())


def test_a_registry_model_needs_the_variable_the_agent_names():
    providers = harness._providers()
    for model in MATRIX["models"]:
        provider = providers.get(model.get("provider")) or {}
        if provider.get("auth_env") and not model.get("baseUrl"):
            assert harness.required_env(model)[0] == provider["auth_env"], model["id"]


def test_no_model_repeats_a_key_variable_the_registry_names():
    named = {p.get("auth_env") for p in harness._providers().values()} - {None}
    for model in MATRIX["models"]:
        assert not named & set(model.get("envRequires", [])), model["id"]
