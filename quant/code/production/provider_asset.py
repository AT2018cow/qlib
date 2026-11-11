"""Provider asset materialization shared by research and website workers.

Kept Modal-free so the website semantic closure does not import a Modal app.
"""

from __future__ import annotations

from pathlib import Path


def download_provider(asset):
    import hashlib
    import requests
    import json
    from provider_archive import safe_extract_provider_archive

    if asset.get("local_path"):
        local = Path(asset["local_path"])
        if not (local / "calendars" / "day.txt").is_file():
            raise ValueError(f"local provider is incomplete: {local}")
        return local

    root = Path("/tmp") / f"october-provider-{asset['digest'].split(':',1)[1][:16]}"
    marker = root / "ready.json"
    if marker.is_file():
        receipt = json.loads(marker.read_text())
        if receipt.get("digest") != asset["digest"]:
            raise ValueError("provider scratch cache identity mismatch")
        existing = root / receipt["relative_root"]
        if not (existing / "calendars/day.txt").is_file():
            raise ValueError("provider scratch cache incomplete")
        return existing
    archive = root.with_suffix(".tar.gz")
    digest = hashlib.sha256()
    written = 0
    mark = 0
    if asset.get("archive_path"):
        with Path(asset["archive_path"]).open("rb") as response, archive.open("wb") as stream:
            for chunk in iter(lambda: response.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
                stream.write(chunk)
                written += len(chunk)
    elif asset.get("url"):
        with requests.get(asset["url"], stream=True, timeout=(30, 180)) as response:
            response.raise_for_status()
            with archive.open("wb") as stream:
                for chunk in response.iter_content(8 * 1024 * 1024):
                    digest.update(chunk)
                    stream.write(chunk)
                    written += len(chunk)
                    if written // (64 * 1024 * 1024) > mark:
                        mark = written // (64 * 1024 * 1024)
                        print(f"[provider] downloaded {written//1024**2}MiB", flush=True)
    else:
        raise ValueError("provider asset has neither archive_path nor url")
    if written != asset["size"] or "sha256:" + digest.hexdigest() != asset["digest"]:
        raise ValueError("provider archive size/SHA256 mismatch")
    print("[provider] digest passed; extracting to container scratch", flush=True)
    extracted = Path(safe_extract_provider_archive(archive, root))
    marker.write_text(json.dumps({"digest": asset["digest"], "relative_root": str(extracted.relative_to(root))}))
    return extracted
