from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any

from google.cloud import storage


class AssetStorage:
    def __init__(self, bucket_name: str) -> None:
        if not bucket_name:
            raise ValueError(
                "FSTAK_GCS_BUCKET_NAME must be set; AssetStorage cannot operate without a bucket"
            )
        self._bucket_name = bucket_name
        self._client = storage.Client()

    async def upload_dist(self, prefix: str, dist_dir: Path) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for path in sorted(dist_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(dist_dir).as_posix()
            content = path.read_bytes()
            files.append(
                {
                    "path": rel,
                    "sha256": f"sha256:{hashlib.sha256(content).hexdigest()}",
                    "size_bytes": len(content),
                    "content_type": mimetypes.guess_type(rel)[0] or "application/octet-stream",
                }
            )
            await asyncio.to_thread(self._upload_file_sync, prefix, rel, path)

        artifact_hash = f"sha256:{hashlib.sha256(json.dumps(files, sort_keys=True).encode('utf-8')).hexdigest()}"
        return {"files": files, "artifact_hash": artifact_hash}

    def _upload_file_sync(self, prefix: str, rel: str, source_path: Path) -> None:
        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(f"{prefix}/{rel}")
        blob.cache_control = "public, max-age=60"
        blob.upload_from_filename(str(source_path))
        blob.make_public()
