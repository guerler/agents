"""What a session runs under: the model, its provider and key, the tool families, Galaxy."""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import subject  # noqa: E402


def _providers():
    """The agent's provider registry, as it publishes it."""
    return subject.describe().get("providers") or {}


def declared_base(model):
    """The endpoint a model entry names, with an `${ENV}` reference resolved; Galaxy's own proxy
    for the `galaxy` provider, as the plugin reaches it."""
    base = model.get("baseUrl") or ""
    if base.startswith("${") and base.endswith("}"):
        base = os.environ.get(base[2:-1], "")
    if not base and model.get("provider") == "galaxy":
        root = os.environ.get("GALAXY_URL", "").strip().rstrip("/")
        base = f"{root}/api/plugins/olit" if root else ""
    return base


def upstream(model):
    """Where this model's completions actually go, which is what a recorder stands in for."""
    provider = _providers().get(model.get("provider"))
    return declared_base(model) or (provider["base_url"] if provider else None)


def build_config(model, capabilities=None, base_url=None):
    """Resolve through the agent's provider registry, so evals and the app agree."""
    # Shared scenarios carry loom's restricted surface; others get the full one.
    capabilities = capabilities or os.environ.get("OLIT_EVAL_CAPABILITIES", "llm,local,read,write")
    base = base_url or declared_base(model)
    config = {
        "galaxy_root": "http://stub.invalid/",
        "ai_provider": model.get("provider"),
        "ai_model": model["model"],
        # Write is granted, or "did not execute" would assert about an unadvertised tool.
        "capabilities": capabilities.split(","),
    }
    if base:
        config["ai_base_url"] = base.rstrip("/")
    max_steps = os.environ.get("OLIT_EVAL_MAX_STEPS", "").strip()
    if max_steps:
        config["max_steps"] = int(max_steps)
    # An output ceiling for endpoints that reserve credit against the model's maximum otherwise.
    max_tokens = os.environ.get("OLIT_EVAL_MAX_TOKENS", "").strip()
    if max_tokens:
        config["ai_max_tokens"] = int(max_tokens)
    galaxy_root = os.environ.get("GALAXY_URL", "").strip()
    config["galaxy_root"] = galaxy_root.rstrip("/") + "/"
    config["galaxy_key"] = os.environ.get("GALAXY_API_KEY", "")
    key = _api_key(model)
    if key:
        config["ai_api_key"] = key
    return config


def required_env(model):
    """What a model needs set to run: its key variable as the registry names it, and envRequires."""
    provider = _providers().get(model.get("provider"))
    names = [provider["auth_env"]] if provider and provider.get("auth_env") else []
    return names + [v for v in model.get("envRequires", []) if v not in names]


def _api_key(model):
    """The registry names the env var; envRequires is the fallback for custom entries."""
    if model.get("provider") == "galaxy":
        # Galaxy's proxy authenticates the Galaxy user, as every other Galaxy request does.
        return os.environ.get("GALAXY_API_KEY", "")
    provider = _providers().get(model.get("provider"))
    if provider and provider.get("auth_env"):
        return os.environ.get(provider["auth_env"], "")
    for name in model.get("envRequires", []):
        if name.endswith("_KEY"):
            return os.environ.get(name, "")
    return ""
