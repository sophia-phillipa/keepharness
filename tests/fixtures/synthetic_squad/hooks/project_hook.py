"""Write a harmless project-hook marker for the isolated conformance fixture."""

import json
import os
import sys
from pathlib import Path

json.load(sys.stdin)
Path(os.environ["SYNTHETIC_HOOK_LOG"]).open("a", encoding="utf-8").write("project-hook\n")
print(json.dumps({}))
