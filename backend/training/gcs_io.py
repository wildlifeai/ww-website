# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Move a folder to and from ``gs://`` prefixes. Local paths pass straight through.

The job's identity comes from the Cloud Run service account (Application Default
Credentials); nothing here reads a key file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple


def is_gs(uri: str) -> bool:
    return str(uri).startswith("gs://")


def split_gs(uri: str) -> Tuple[str, str]:
    """``gs://bucket/some/prefix`` → ``("bucket", "some/prefix")``."""
    if not is_gs(uri):
        raise ValueError(f"not a gs:// URI: {uri}")
    rest = uri[len("gs://") :]
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        raise ValueError(f"gs:// URI has no bucket: {uri}")
    return bucket, prefix.strip("/")


def download_prefix(uri: str, dest: Path) -> int:
    """Every object under the prefix into ``dest`` (relative paths kept). Returns the count."""
    from google.cloud import storage

    bucket_name, prefix = split_gs(uri)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    bucket = storage.Client().bucket(bucket_name)
    count = 0
    for blob in bucket.list_blobs(prefix=prefix + "/" if prefix else None):
        rel = blob.name[len(prefix) + 1 :] if prefix else blob.name
        if not rel or rel.endswith("/"):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))
        count += 1
    return count


def upload_dir(src: Path, uri: str) -> int:
    """Every file under ``src`` to the prefix (relative paths kept). Returns the count."""
    from google.cloud import storage

    bucket_name, prefix = split_gs(uri)
    src = Path(src)
    bucket = storage.Client().bucket(bucket_name)
    count = 0
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        name = f"{prefix}/{path.relative_to(src).as_posix()}" if prefix else path.relative_to(src).as_posix()
        bucket.blob(name).upload_from_filename(str(path))
        count += 1
    return count


def stage_input(uri: str, scratch: Path) -> Path:
    """A local directory holding the input: the path itself, or a download of the prefix."""
    if not is_gs(uri):
        return Path(uri)
    dest = Path(scratch) / "input"
    n = download_prefix(uri, dest)
    print(f"[gcs] downloaded {n} objects from {uri}")
    return dest


def publish_output(local_dir: Path, uri: str) -> None:
    if not is_gs(uri):
        return
    n = upload_dir(local_dir, uri)
    print(f"[gcs] uploaded {n} files to {uri}")
