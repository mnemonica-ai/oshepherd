"""Installed runtime materialization is deterministic and manifest restricted."""

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from oshepherd.backends.supabase import releases, runtime_bundle
from oshepherd.backends.supabase.releases import OperatorError


@pytest.fixture
def installed(tmp_path, monkeypatch):
    package = tmp_path / "installed" / "oshepherd"
    root = package / "backends/supabase/release"
    root.mkdir(parents=True)
    (package / "__init__.py").write_text("VERSION = '1.2.3'\n")
    (package / "secret.env").write_text("NEVER_UPLOAD=private")
    (root / "template.txt").write_text("verified template")
    manifest = {
        "python_sources": {
            "__init__.py": hashlib.sha256(
                (package / "__init__.py").read_bytes()
            ).hexdigest()
        },
        "artifacts": {
            "template.txt": hashlib.sha256(
                (root / "template.txt").read_bytes()
            ).hexdigest()
        },
        "migrations": [],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    licenses = package.parent / "oshepherd-1.2.3.dist-info/licenses"
    licenses.mkdir(parents=True)
    (licenses / "LICENSE").write_text("Copyright fixture. Permission is granted.\n")
    (licenses / "private-unlisted.txt").write_text("NEVER_UPLOAD")

    class Distribution:
        version = "1.2.3"
        metadata = {"Name": "oshepherd"}
        editable = False
        license_name = "LICENSE"

        def locate_file(self, name):
            return package.parent / name

        def read_text(self, name):
            if name == "direct_url.json":
                return json.dumps({"dir_info": {"editable": self.editable}})
            if name == "METADATA":
                return (
                    "Metadata-Version: 2.4\nName: oshepherd\nVersion: 1.2.3\nRequires-Python: >=3.12\nLicense-File: "
                    + self.license_name
                    + "\n"
                )

    dist = Distribution()
    monkeypatch.setattr(runtime_bundle, "distribution", lambda name: dist)
    monkeypatch.setattr(runtime_bundle, "files", lambda name: package)
    monkeypatch.setattr(
        releases,
        "files",
        lambda name: package if name == "oshepherd" else package / "backends/supabase",
    )
    return package, dist


def test_runtime_wheel_is_deterministic_and_record_hashes_match(installed, tmp_path):
    wheel = runtime_bundle.materialize_current_wheel(tmp_path / "runtime")
    first = wheel.read_bytes()
    assert (
        runtime_bundle.materialize_current_wheel(tmp_path / "runtime").read_bytes()
        == first
    )
    with zipfile.ZipFile(wheel) as archive:
        assert "oshepherd/secret.env" not in archive.namelist()
        assert (
            archive.read("oshepherd-1.2.3.dist-info/licenses/LICENSE")
            == b"Copyright fixture. Permission is granted.\n"
        )
        assert (
            "oshepherd-1.2.3.dist-info/licenses/private-unlisted.txt"
            not in archive.namelist()
        )
        assert (
            archive.read("oshepherd-1.2.3.dist-info/entry_points.txt")
            == b"[console_scripts]\noshepherd = oshepherd.cli:main\n"
        )
        rows = list(
            csv.reader(
                io.StringIO(archive.read("oshepherd-1.2.3.dist-info/RECORD").decode())
            )
        )
        assert len(rows) == len(archive.namelist())
        for name, digest, length in rows:
            if not digest:
                continue
            data = archive.read(name)
            assert digest == "sha256=" + base64.urlsafe_b64encode(
                hashlib.sha256(data).digest()
            ).decode().rstrip("=")
            assert length == str(len(data))


def test_editable_and_tampered_package_refused(installed, tmp_path):
    package, dist = installed
    dist.editable = True
    with pytest.raises(OperatorError, match="installed_distribution_required"):
        runtime_bundle.materialize_current_wheel(tmp_path / "runtime")
    dist.editable = False
    (package / "__init__.py").write_text("tampered")
    with pytest.raises(OperatorError, match="release_hash_mismatch"):
        runtime_bundle.materialize_current_wheel(tmp_path / "runtime")
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../secret.env",
        "/absolute/LICENSE",
        "nested/../LICENSE",
        "nested\\LICENSE",
        "missing.txt",
    ],
)
def test_invalid_or_missing_declared_license_fails_closed(installed, tmp_path, name):
    _, dist = installed
    dist.license_name = name
    with pytest.raises(OperatorError, match="invalid_installed_distribution_license"):
        runtime_bundle.materialize_current_wheel(tmp_path / "runtime")
    assert not (tmp_path / "runtime").exists()


def test_symlink_license_is_not_followed(installed, tmp_path):
    package, _ = installed
    target = package.parent / "oshepherd-1.2.3.dist-info/licenses/LICENSE"
    target.unlink()
    target.symlink_to(package / "secret.env")
    with pytest.raises(OperatorError, match="invalid_installed_distribution_license"):
        runtime_bundle.materialize_current_wheel(tmp_path / "runtime")
