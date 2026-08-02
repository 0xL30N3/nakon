"""Deterministic archives.

Two independent builds of the same content must produce byte-identical archives, otherwise
"reproducible" is a claim we can't check. That means stripping everything the filesystem
contributes: mtimes, uid/gid, owner names, directory iteration order, and the gzip header's
own timestamp.

Note that bundle identity is *not* derived from these bytes — ids come from canonical JSON
(see nakon.hashing), so a tar or gzip library upgrade can never renumber existing bundles.
Determinism here is for diffing and for trustworthy transport checksums.
"""

import gzip
import io
import os
import tarfile
import zipfile
from pathlib import Path


def _normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    # Executable bit for scripts, plain 0644 for everything else; nothing else survives.
    info.mode = 0o755 if (info.mode & 0o100) else 0o644
    if info.isdir():
        info.mode = 0o755
    return info


def _walk_sorted(root: Path):
    """Yield (absolute_path, arcname) depth-first in a stable, locale-independent order."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for name in dirnames + filenames:
            full = Path(dirpath) / name
            entries.append((full, str(full.relative_to(root))))
    entries.sort(key=lambda pair: pair[1])
    return entries


def write_tar_gz(source_dir: Path, dest: Path) -> None:
    """Archive a directory's contents (not the directory itself) deterministically."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for full, arcname in _walk_sorted(source_dir):
            info = _normalize(tar.gettarinfo(str(full), arcname=arcname))
            if info.isreg():
                with open(full, "rb") as handle:
                    tar.addfile(info, handle)
            else:
                tar.addfile(info)

    dest.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so the gzip header itself is stable, and a fixed compresslevel so the output
    # doesn't shift with a zlib default change.
    with open(dest, "wb") as out:
        with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6, mtime=0) as gz:
            gz.write(raw.getvalue())


def write_zip(source_dir: Path, dest: Path) -> None:
    """Deterministic zip, for Windows targets (no tar on a stock Windows box)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for full, arcname in _walk_sorted(source_dir):
            if full.is_dir():
                continue
            info = zipfile.ZipInfo(arcname.replace(os.sep, "/"), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, full.read_bytes())
