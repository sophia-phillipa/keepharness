"""P5 §9: catch a real provider CLI's catalog drifting away from what the harness assumes.

Two kinds of checks live here:

* Offline, always runs: the ``LEGACY_MODELS`` freshness note in ``adapters/claude/account.py``
  must be a "Reviewed YYYY-MM-DD" date no more than 90 days old — a stale review is a paper
  cut, not a live-provider problem, so it is not gated behind ``TAIL_HARNESS_LIVE``.
* Live, opt-in (``TAIL_HARNESS_LIVE=1``): spawn the real ``claude``/``codex`` CLI (no
  inference, metadata only) and fail if a model the harness relies on has quietly
  disappeared, been renamed, or been disabled. Cost: two CLI spawns, no tokens.
"""

import asyncio
import datetime
import re
import shutil

import pytest

# Not ``import adapters.claude.account as claude_account``: adapters/__init__.py's own
# ``from .claude import backend as claude`` shadows the ``adapters.claude`` attribute
# with the backend module (F-33 in the P5 findings log), so a dotted-with-alias import
# resolves the wrong module. ``from adapters.claude import account`` is unaffected.
from adapters.claude import account as claude_account
from adapters.codex import rpc as codex_rpc

REVIEWED_RE = re.compile(r"Reviewed (\d{4}-\d{2}-\d{2})")
MAX_REVIEW_AGE_DAYS = 90

# Model ids the harness's own project defaults assume codex/model-list will keep
# returning; kept in sync with adapters/codex/specs/models/*.md (excluding the two
# non-model reference docs in that folder).
CODEX_SPEC_NON_MODEL_DOCS = {"cli-catalog.md", "project-identifiers.md"}


def test_claude_legacy_models_review_note_is_not_stale():
    """The ``Reviewed YYYY-MM-DD`` note above ``LEGACY_MODELS`` must be recent."""
    import inspect

    source = inspect.getsource(claude_account)
    match = REVIEWED_RE.search(source)
    assert match, "expected a 'Reviewed YYYY-MM-DD' comment above LEGACY_MODELS"
    reviewed = datetime.date.fromisoformat(match.group(1))
    age = (datetime.date.today() - reviewed).days
    assert age <= MAX_REVIEW_AGE_DAYS, (
        f"adapters/claude/account.py's LEGACY_MODELS review note is {age} days old "
        f"(reviewed {reviewed}); re-check against the model-deprecations and "
        "model-config docs cited in the comment and bump the date"
    )


def _codex_spec_model_ids():
    specs_dir = codex_rpc.__file__
    from pathlib import Path

    models_dir = Path(specs_dir).resolve().parent / "specs" / "models"
    ids = []
    for path in sorted(models_dir.glob("*.md")):
        if path.name in CODEX_SPEC_NON_MODEL_DOCS:
            continue
        heading = path.read_text().splitlines()[0]
        match = re.search(r"Codex model ID:\s*`?([^`\n]+)`?", heading)
        ids.append(match.group(1).strip() if match else path.stem)
    return ids


@pytest.mark.live
def test_claude_initialize_catalog_still_covers_the_legacy_models():
    """A LEGACY_MODELS entry must not have silently become disabled or vanished."""
    binary = shutil.which("claude")
    assert binary, "claude CLI not found on PATH; this test only runs with TAIL_HARNESS_LIVE=1"

    async def scenario():
        data = await claude_account.metadata({"binary": binary})
        return data, claude_account.model_catalog(data)

    data, catalog = asyncio.run(
        scenario()
    )  # model_catalog raises claude_catalog_unavailable on empty

    disabled = set()
    for item in data.get("models", []):
        if item.get("disabled"):
            disabled.update(filter(None, (item.get("value"), item.get("resolvedModel"))))

    regressed = [
        model
        for model in claude_account.LEGACY_MODELS
        if model.removesuffix("[1m]") in disabled or model in disabled
    ]
    assert not regressed, f"models now reported disabled by the CLI: {regressed}"
    assert catalog, "model_catalog() returned an empty catalog"


@pytest.mark.live
def test_codex_model_list_still_returns_every_spec_model():
    """Every model documented under adapters/codex/specs/models/*.md must still resolve."""
    binary = shutil.which("codex")
    assert binary, "codex CLI not found on PATH; this test only runs with TAIL_HARNESS_LIVE=1"

    response = asyncio.run(codex_rpc.metadata(binary, "model/list"))
    returned_ids = {
        value
        for item in response.get("data", [])
        for value in (item.get("id"), item.get("model"))
        if value
    }

    expected_ids = _codex_spec_model_ids()
    missing = [model_id for model_id in expected_ids if model_id not in returned_ids]
    assert not missing, f"documented codex models no longer returned by model/list: {missing}"

    new_ids = sorted(returned_ids - set(expected_ids))
    if new_ids:
        print(f"WARNING: codex model/list now returns undocumented model ids: {new_ids}")
