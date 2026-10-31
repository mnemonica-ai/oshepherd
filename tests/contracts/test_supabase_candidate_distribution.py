"""Artifact gates exercise archive contents, source drift, and experimental versions."""

import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest

from scripts.verify_candidate import (
    CandidateError,
    archive_files,
    sha256,
    verify_distributions,
)


def candidate(tmp_path, version="0.0.25a1", *, wheel_extra=None, sdist_extra=None):
    source, dist = tmp_path / "source", tmp_path / "dist"
    source.mkdir()
    dist.mkdir()
    package = source / "oshepherd"
    package.mkdir()
    content = b"def main(): pass\n"
    (package / "cli.py").write_bytes(content)
    release = package / "backends/supabase/release"
    release.mkdir(parents=True)
    template = b"# packaged Vercel app\n"
    (release / "templates/vercel").mkdir(parents=True)
    (release / "templates/vercel/app.py").write_bytes(template)
    manifest = {
        "python_sources": {"cli.py": sha256(content)},
        "artifacts": {"templates/vercel/app.py": sha256(template)},
        "migrations": [],
        "profiles": {
            "edge_free": {"qualified": False},
            "vercel_hobby": {"qualified": False},
        },
    }
    manifest_bytes = json.dumps(manifest).encode()
    (release / "manifest.json").write_bytes(manifest_bytes)
    (source / "pyproject.toml").write_text(
        '[project]\nname="oshepherd"\nversion="' + version + '"\n'
    )
    metadata = f"Metadata-Version: 2.3\nName: oshepherd\nVersion: {version}\n".encode()
    files = {
        "oshepherd/cli.py": content,
        "oshepherd/backends/supabase/release/manifest.json": manifest_bytes,
        "oshepherd/backends/supabase/release/templates/vercel/app.py": template,
    }
    wheel = dist / f"oshepherd-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, value in {
            **files,
            f"oshepherd-{version}.dist-info/METADATA": metadata,
            f"oshepherd-{version}.dist-info/entry_points.txt": b"[console_scripts]\noshepherd = oshepherd.cli:main\n",
            **(wheel_extra or {}),
        }.items():
            archive.writestr(name, value)
    sdist = dist / f"oshepherd-{version}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for name, value in {
            **files,
            "PKG-INFO": metadata,
            **(sdist_extra or {}),
        }.items():
            member = tarfile.TarInfo("oshepherd-" + version + "/" + name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return source, dist


@pytest.mark.parametrize("version", ["0.0.25a1", "0.0.25rc1", "0.0.25.dev1"])
def test_experimental_versions_keep_unqualified_profiles_and_hashes(tmp_path, version):
    source, dist = candidate(tmp_path, version)
    report = verify_distributions(dist, source=source, require_prerelease=True)
    assert report["version"] == version
    assert report["profiles"] == {"edge_free": False, "vercel_hobby": False}
    assert report["files"] == {
        path.name: sha256(path.read_bytes()) for path in dist.iterdir()
    }


def test_stable_version_is_buildable_but_cannot_publish_experiment(tmp_path):
    source, dist = candidate(tmp_path, "0.0.24")
    assert verify_distributions(dist, source=source)["prerelease"] is False
    with pytest.raises(
        CandidateError, match="experimental_publish_requires_prerelease"
    ):
        verify_distributions(dist, source=source, require_prerelease=True)


def test_local_candidate_version_is_allowed_only_for_local_verification(tmp_path):
    source, dist = candidate(tmp_path, "0.0.25a1+local")
    assert verify_distributions(dist, source=source)["version"] == "0.0.25a1+local"
    with pytest.raises(CandidateError, match="local_version_cannot_publish"):
        verify_distributions(dist, source=source, require_prerelease=True)


@pytest.mark.parametrize(
    "private",
    [
        ".oshepherd/token.json",
        ".env",
        "tests/fixtures.py",
        "oshepherd/worker-session.json",
        "../leak",
        "oshepherd/config.env",
    ],
)
def test_wheel_rejects_private_or_unsafe_paths(tmp_path, private):
    source, dist = candidate(tmp_path, wheel_extra={private: b"secret"})
    with pytest.raises(CandidateError, match="private_or_unsafe_archive_path"):
        verify_distributions(dist, source=source)


def test_sdist_cannot_hide_private_files(tmp_path):
    source, dist = candidate(
        tmp_path, sdist_extra={".oshepherd/provider.json": b"private"}
    )
    with pytest.raises(CandidateError, match="private_or_unsafe_archive_path"):
        verify_distributions(dist, source=source)


def test_unmanifested_executable_and_source_drift_are_rejected(tmp_path):
    source, dist = candidate(
        tmp_path, wheel_extra={"oshepherd/forgotten.py": b"print('stale')"}
    )
    with pytest.raises(CandidateError, match="unexpected_packaged_file"):
        verify_distributions(dist, source=source)
    (source / "oshepherd/cli.py").write_text("def main(): print('changed')\n")
    with pytest.raises(CandidateError, match="source_manifest_hash_mismatch"):
        verify_distributions(dist, source=source)


def test_sdist_package_must_match_verified_wheel_source(tmp_path):
    source, dist = candidate(tmp_path, sdist_extra={"oshepherd/cli.py": b"different"})
    with pytest.raises(CandidateError, match="distribution_source_or_asset_mismatch"):
        verify_distributions(dist, source=source)


def test_extra_distribution_file_cannot_be_published_unverified(tmp_path):
    source, dist = candidate(tmp_path)
    (dist / "extra.whl").write_bytes(b"unverified")
    with pytest.raises(CandidateError, match="exactly_one_wheel_and_sdist_required"):
        verify_distributions(dist, source=source)


def test_uv_build_marker_is_allowed_but_other_hidden_content_is_rejected(tmp_path):
    source, dist = candidate(tmp_path)
    marker = dist / ".gitignore"
    marker.write_bytes(b"*")
    assert ".gitignore" not in verify_distributions(dist, source=source)["files"]
    marker.write_bytes(b"private state")
    with pytest.raises(CandidateError, match="exactly_one_wheel_and_sdist_required"):
        verify_distributions(dist, source=source)


def test_tar_links_are_never_followed(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("root/link")
        member.type, member.linkname = tarfile.SYMTYPE, "/private/secret"
        output.addfile(member)
    with pytest.raises(CandidateError, match="archive_link_rejected"):
        archive_files(archive)
