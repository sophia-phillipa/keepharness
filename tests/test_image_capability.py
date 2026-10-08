"""capabilities.images in /v1/models follows the same rules as validate_images."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from test_workspaces import config

from agent_service.app import Service

DEEPSEEK = {
    "enabled": True,
    "models": ["deepseek-chat"],
    "projects": ["p"],
    "permissions": {"read": True, "upload": True},
}


def listed(tmp_path, *, vision=None, local_mode="native", codex_mode="native"):
    cfg = config(tmp_path)
    cfg["services"]["deepseek"] = dict(DEEPSEEK)
    cfg["services"]["local"]["mode"] = local_mode
    cfg["services"]["codex"]["mode"] = codex_mode
    service = Service(cfg)
    properties = {"modalities": {"vision": vision}} if vision is not None else {}
    try:
        with patch.object(service, "local_properties", AsyncMock(return_value=properties)):
            models = asyncio.run(service.models_with_context("p"))
    finally:
        service.db.close()
    return {model["id"]: model["capabilities"]["images"] for model in models}


def test_native_codex_reads_images_and_deepseek_does_not(tmp_path):
    images = listed(tmp_path)
    assert images["gpt-6-astra"] is True
    assert images["deepseek-chat"] is False


@pytest.mark.parametrize(("vision", "expected"), [(True, True), (False, False), (None, False)])
def test_local_model_needs_vision_from_its_server(tmp_path, vision, expected):
    assert listed(tmp_path, vision=vision)["installed-model"] is expected


def test_local_model_without_reachable_server_is_false(tmp_path):
    cfg = config(tmp_path)
    service = Service(cfg)
    try:
        with patch.object(service, "local_properties", AsyncMock(side_effect=OSError)):
            models = asyncio.run(service.models_with_context("p"))
    finally:
        service.db.close()
    assert [m["capabilities"]["images"] for m in models if m["id"] == "installed-model"] == [False]


def test_retired_service_mode_cannot_hide_native_cloud_image_capability(tmp_path):
    assert listed(tmp_path, codex_mode="scoped")["gpt-6-astra"] is True


def test_list_agrees_with_validate_images(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["deepseek"] = dict(DEEPSEEK)
    service = Service(cfg)
    try:
        for backend, model in (("codex", "gpt-6-astra"), ("deepseek", "deepseek-chat")):
            allowed = [m["capabilities"]["images"] for m in service.models("p") if m["id"] == model]
            try:
                asyncio.run(service.validate_images(backend, model))
                accepted = True
            except Exception:
                accepted = False
            assert allowed == [accepted]
    finally:
        service.db.close()
