"""One JSON file per Harness-owned agent in a private folder.

The file handling is ``JsonFileRepository``; what an agent may contain is decided by
``agent_service.harness_agents``, not here.
"""

import re
from pathlib import Path

from ..errors import APIError
from .json_file_repository import JsonFileRepository

AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
MAX_FILE_BYTES = 65536


def unsafe_storage() -> APIError:
    return APIError("harness_agent_storage_unsafe", 500)


class HarnessAgentRepository(JsonFileRepository):
    def __init__(self, folder: str | Path) -> None:
        super().__init__(
            folder, id_pattern=AGENT_ID, max_bytes=MAX_FILE_BYTES, unsafe=unsafe_storage
        )
