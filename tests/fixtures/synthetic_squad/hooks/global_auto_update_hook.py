"""Write an unrelated global-hook marker without updating anything."""

import json
import os
import sys
from pathlib import Path

json.load(sys.stdin)
Path(os.environ["SYNTHETIC_HOOK_LOG"]).open("a", encoding="utf-8").write(
    "global-auto-update-hook\n"
)
print(json.dumps({}))
