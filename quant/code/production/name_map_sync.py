"""Idempotent code->name mapping sync for the daily cron (R07).

The 2026-10-18 review found the name-map update lived *after* the
``if not missing_files: ... return`` early-return, so same-day reruns (or any
day whose main artifacts already exist) skipped it entirely.  The sync is now
an independent, idempotent task invoked on every completed daily run:

- content-identical mapping -> no commit ("unchanged");
- changed mapping -> one Contents-API update ("updated");
- HTTP/network failure raises and the caller downgrades to a warning; it must
  never block or modify the already-validated signal publication.

The HTTP glue is injected so CI can test the behavior without network access.
"""

from __future__ import annotations

import base64

NAME_MAP_PATH = "quant/data/signals/code_name_map.csv"
DEFAULT_MESSAGE = "chore(signal): update code->name mapping (cron)"


def sync_code_name_map(
    name_csv: str,
    *,
    http_get,
    http_put,
    repo: str,
    branch: str,
    path: str = NAME_MAP_PATH,
    message: str = DEFAULT_MESSAGE,
) -> str:
    """Publish ``name_csv`` as the repo's name map.  Returns "updated" or "unchanged".

    ``http_get(url) -> (status_code, json_or_None)``
    ``http_put(url, json_body) -> status_code``
    """
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    status, payload = http_get(api)
    if status == 200:
        if not isinstance(payload, dict) or "content" not in payload:
            raise RuntimeError("name-map check returned malformed payload")
        old_csv = base64.b64decode(payload["content"]).decode()
        old_sha = payload.get("sha")
    elif status == 404:
        old_csv, old_sha = None, None
    else:
        raise RuntimeError(f"Cannot check name map: HTTP {status}")
    if old_csv == name_csv:
        return "unchanged"
    body = {
        "message": message,
        "content": base64.b64encode(name_csv.encode()).decode(),
        "branch": branch,
    }
    if old_sha:
        body["sha"] = old_sha
    push_status = http_put(api, body)
    if push_status not in (200, 201):
        raise RuntimeError(f"name-map push failed: HTTP {push_status}")
    return "updated"
