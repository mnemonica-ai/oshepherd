"""Rebuild only the verified installed distribution for portable owned runtimes."""

import base64
import csv
from email.parser import Parser
import hashlib
from importlib.metadata import distribution
from importlib.resources import files
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile

from .private_files import private_directory
from .releases import OperatorError, release_assets


def materialize_current_wheel(directory):
    """Return a deterministic pure-Python wheel; never crawl a user directory.

    A uvx installation carries all bytes required by the release manifest. Repack
    those exact bytes and installed distribution metadata, without a build tool,
    registry lookup, repository, cached wheel, credentials or user model files.
    Editable checkouts must first build and install their candidate wheel.
    """
    package = files("oshepherd")
    dist = distribution("oshepherd")
    try:
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        if (
            direct.get("dir_info", {}).get("editable")
            or Path(str(package)).resolve()
            != Path(dist.locate_file("oshepherd")).resolve()
        ):
            raise OperatorError("installed_distribution_required")
        version = dist.version
        if not re.fullmatch(r"[A-Za-z0-9_.+]+", version):
            raise ValueError()
        metadata = dist.read_text("METADATA")
        if not metadata or dist.metadata["Name"].lower() != "oshepherd":
            raise ValueError()
        root, manifest = release_assets()
        contents = {
            "oshepherd/" + name: package.joinpath(name).read_bytes()
            for name in manifest["python_sources"]
        }
        assets = (
            set(manifest["artifacts"])
            | {entry["path"] for entry in manifest["migrations"]}
            | {"manifest.json"}
        )
        for name in assets:
            contents["oshepherd/backends/supabase/release/" + name] = root.joinpath(
                name
            ).read_bytes()
        expected = {
            "oshepherd/" + name: digest
            for name, digest in manifest["python_sources"].items()
        }
        expected.update(
            {
                "oshepherd/backends/supabase/release/" + name: digest
                for name, digest in manifest["artifacts"].items()
            }
        )
        expected.update(
            {
                "oshepherd/backends/supabase/release/" + entry["path"]: entry["sha256"]
                for entry in manifest["migrations"]
            }
        )
        if any(
            hashlib.sha256(contents[name]).hexdigest() != digest
            for name, digest in expected.items()
        ):
            raise OperatorError("release_hash_mismatch")
        info = "oshepherd-" + version + ".dist-info/"
        contents[info + "METADATA"] = metadata.encode()
        licenses = Parser().parsestr(metadata).get_all("License-File", [])
        if len(licenses) > 16 or len(licenses) != len(set(licenses)):
            raise OperatorError("invalid_installed_distribution_license")
        for license_name in licenses:
            relative = PurePosixPath(license_name)
            if (
                not license_name
                or relative.is_absolute()
                or "\\" in license_name
                or any(part in ("", ".", "..") for part in license_name.split("/"))
                or any(ord(char) < 32 for char in license_name)
            ):
                raise OperatorError("invalid_installed_distribution_license")
            archive_name = info + "licenses/" + license_name
            license_path = Path(dist.locate_file(archive_name))
            license_root = Path(dist.locate_file(info + "licenses"))
            if (
                license_path.is_symlink()
                or license_root.is_symlink()
                or any(
                    parent.is_symlink()
                    for parent in license_path.parents
                    if parent != license_root and license_root in parent.parents
                )
                or not license_path.is_file()
                or license_path.stat().st_size > 1024 * 1024
            ):
                raise OperatorError("invalid_installed_distribution_license")
            contents[archive_name] = license_path.read_bytes()
        contents[info + "WHEEL"] = (
            b"Wheel-Version: 1.0\nGenerator: oshepherd\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        contents[info + "entry_points.txt"] = (
            b"[console_scripts]\noshepherd = oshepherd.cli:main\n"
        )
        record = io.StringIO(newline="")
        writer = csv.writer(record, lineterminator="\n")
        for name, value in sorted(contents.items()):
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(value).digest())
                .decode()
                .rstrip("=")
            )
            writer.writerow([name, "sha256=" + digest, str(len(value))])
        writer.writerow([info + "RECORD", "", ""])
        contents[info + "RECORD"] = record.getvalue().encode()
        directory = private_directory(directory)
        path = directory / ("oshepherd-" + version + "-py3-none-any.whl")
        descriptor, temporary = tempfile.mkstemp(prefix=".wheel-", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                with zipfile.ZipFile(
                    stream, "w", compression=zipfile.ZIP_DEFLATED
                ) as archive:
                    for name, value in sorted(contents.items()):
                        member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                        member.compress_type = zipfile.ZIP_DEFLATED
                        member.external_attr = 0o644 << 16
                        archive.writestr(member, value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise OperatorError("invalid_installed_distribution") from None
