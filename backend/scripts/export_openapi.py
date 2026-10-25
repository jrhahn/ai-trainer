"""Print the API's OpenAPI schema, for the frontend's generated types (ai-trainer-ops#276).

Usage (from ``backend/``): ``python scripts/export_openapi.py``. The frontend's
``npm run types:api`` runs it and writes ``src/types/api.gen.ts`` from it.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402

json.dump(app.openapi(), sys.stdout, indent=1, sort_keys=True)
