"""Checks that a user-defined tool names a container image that exists."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .base import Failure

# The YAML key a user-defined tool declares its image under, as the skill defines it.
CONTAINER_LINE = re.compile(r"^\s*container:\s*[\"']?(?:docker://)?([^\s\"']+)", re.M)
QUAY_BIOCONTAINER = re.compile(r"^quay\.io/biocontainers/([^:/]+):(.+)$")
SINGULARITY_DEPOT = "depot.galaxyproject.org/singularity/"


def _image_exists(image):
    """Whether the registry really has this image. None when it is not verifiable here.

    Lets a network failure raise: an unreachable registry is an environment problem, and
    grading it as the agent's mistake would be worse than recording an error.
    """
    match = QUAY_BIOCONTAINER.match(image)
    if match:
        name, tag = match.groups()
        url = (
            f"https://quay.io/api/v1/repository/biocontainers/{urllib.parse.quote(name)}"
            f"/tag/?onlyActiveTags=true&specificTag={urllib.parse.quote(tag)}"
        )
        with urllib.request.urlopen(url, timeout=30) as response:
            return bool((json.load(response) or {}).get("tags"))
    if image.startswith(SINGULARITY_DEPOT):
        request = urllib.request.Request("https://" + image, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status == 200
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise
    return None


def _container(spec, run, failures, exercised):
    """A tool's image, checked against the registry rather than against a pattern.

    An invented `--<hash>_<build>` suffix reads exactly like a real one and fails only when
    the job tries to pull it, so nothing short of asking the registry can tell them apart.
    """
    if not spec:
        return
    exercised.add("behavior")
    declared = list(dict.fromkeys(CONTAINER_LINE.findall(run.chat_text or "")))
    if not declared:
        failures.append(Failure("container.declared", "the reply declares no container", "behavior"))
        return
    if not spec.get("resolves"):
        return
    for image in declared:
        exists = _image_exists(image)
        if exists is None:
            failures.append(
                Failure(
                    "container.resolves",
                    f"{image!r} is neither a quay.io biocontainer nor a singularity depot image, "
                    "so nothing can confirm it exists",
                    "behavior",
                )
            )
        elif not exists:
            failures.append(
                Failure(
                    "container.resolves",
                    f"{image!r} does not exist; the tag reads as real and would fail to pull",
                    "behavior",
                )
            )
