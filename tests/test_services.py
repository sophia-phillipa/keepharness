"""The service layer keeps the historical Service surface while delegating."""

from unittest.mock import patch

from test_workspaces import config

from agent_service import app
from agent_service.config import runtime_job_affected, validate_runtime_config
from agent_service.services import queue_worker
from agent_service.services.conversation_service import ConversationService


def test_service_alias_and_project_folder_sets_are_shared(tmp_path):
    assert app.Service is ConversationService
    service = ConversationService(config(tmp_path))
    try:
        projects = service.project_service
        assert service.deleted_project_folders is projects.deleted_project_folders
        assert service.deleting_project_folders is projects.deleting_project_folders
        assert service.config is projects.config
        service.deleting_project_folders.add("p")
        assert "p" in projects.deleting_project_folders
    finally:
        service.db.close()


def test_queue_methods_delegate_to_the_queue_worker(tmp_path):
    service = ConversationService(config(tmp_path))
    try:
        with patch.object(queue_worker, "next_job", return_value="row") as next_job:
            assert service.next_job() == "row"
        next_job.assert_called_once_with(service)
        with patch.object(queue_worker, "cancel", return_value={"ok": True}) as cancel:
            assert service.cancel(("a", {}), "job") == {"ok": True}
        cancel.assert_called_once_with(service, ("a", {}), "job")
    finally:
        service.db.close()


def test_runtime_config_helpers_are_pure_functions(tmp_path):
    current = config(tmp_path)
    assert validate_runtime_config(current)
    assert not validate_runtime_config({**current, "services": []})
    row = {"id": "j", "owner": "a", "project": "p", "state": "queued", "payload": "{}"}
    assert runtime_job_affected(current, {}, row, {**current, "projects": {}})
