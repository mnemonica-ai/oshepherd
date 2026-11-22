"""Owned, bounded private files for enrollment and local operator state."""

import json
import os
from pathlib import Path
import stat
import tempfile


def private_directory(path):
    path = Path(path).absolute()
    # Check ancestors too: O_NOFOLLOW only protects the final component.
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Private directories cannot be symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Private directory must be owned by this user")
    path.chmod(0o700)
    return path


def read_private_json(filename, *, limit=65536):
    path = Path(filename).absolute()
    if any(item.is_symlink() for item in path.parents):
        raise ValueError("Private directories cannot be symlinks")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW)) as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ValueError(
                "Private state must be an owned regular file with mode 0600"
            )
        content = stream.read(limit + 1)
        if len(content.encode("utf-8")) > limit:
            raise ValueError("Private state exceeds its size limit")
        return json.loads(content)


def output_directory(path):
    """Validate an existing output parent without changing its permissions."""
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Private directories cannot be symlinks")
    metadata = path.stat()
    if (
        metadata.st_uid != os.getuid()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o022
        or metadata.st_mode & 0o300 != 0o300
    ):
        raise ValueError(
            "Output directory must be owned and writable only by this user"
        )
    return path


def write_private(filename, content, *, replace=True):
    path = Path(filename).absolute()
    if replace:
        parent = private_directory(path.parent)
        if path.is_symlink():
            raise ValueError("Private state cannot be a symlink")
        if path.exists():
            metadata = path.stat()
            if metadata.st_uid != os.getuid() or not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Private state must be an owned regular file")
    else:
        parent = output_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".private-", dir=parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            if not replace:
                os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_private_json(filename, value, *, replace=True):
    write_private(filename, json.dumps(value), replace=replace)
