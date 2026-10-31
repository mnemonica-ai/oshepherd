"""Build and verify a candidate without publishing or requiring cloud services.

uv run --locked python -m scripts.verify_candidate --build --dist /tmp/candidate
Use --require-prerelease only for the explicit experimental publication gate.
"""

import argparse
import configparser
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import os
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "oshepherd/backends/supabase/release/manifest.json"
FORBIDDEN_PARTS = {
    ".oshepherd",
    ".git",
    ".venv",
    ".aws",
    ".ssh",
    ".codex",
    ".agents",
    ".codegraph",
    ".env",
    ".temp",
    ".branches",
    "__pycache__",
    "tests",
}
MAX_FILE = 32 * 1024 * 1024
MAX_ARCHIVE = 128 * 1024 * 1024


class CandidateError(ValueError):
    pass


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or any(part in FORBIDDEN_PARTS for part in path.parts)
        or name.endswith((".env", ".worker-session.json", ".pyc"))
        or path.name
        in {
            "connection.json",
            "edge-provider.json",
            "vercel-provider.json",
            "worker-session.json",
        }
    ):
        raise CandidateError("private_or_unsafe_archive_path")
    return name


def archive_files(path):
    values = {}
    total = 0
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                safe_name(entry.filename)
                if entry.is_dir():
                    continue
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise CandidateError("archive_link_rejected")
                if entry.filename in values or entry.file_size > MAX_FILE:
                    raise CandidateError("archive_size_or_duplicate")
                total += entry.file_size
                if total > MAX_ARCHIVE:
                    raise CandidateError("archive_size_or_duplicate")
                values[entry.filename] = archive.read(entry)
    else:
        with tarfile.open(path, "r:gz") as archive:
            roots = set()
            for entry in archive.getmembers():
                safe_name(entry.name)
                parts = PurePosixPath(entry.name).parts
                if not parts:
                    raise CandidateError("invalid_sdist_root")
                roots.add(parts[0])
                if entry.isdir():
                    continue
                if not entry.isfile() or len(parts) < 2:
                    raise CandidateError("archive_link_rejected")
                name = "/".join(parts[1:])
                if name in values or entry.size > MAX_FILE:
                    raise CandidateError("archive_size_or_duplicate")
                total += entry.size
                if total > MAX_ARCHIVE:
                    raise CandidateError("archive_size_or_duplicate")
                values[name] = archive.extractfile(entry).read()
            if len(roots) != 1:
                raise CandidateError("invalid_sdist_root")
    return values


def version_metadata(content):
    metadata = BytesParser().parsebytes(content)
    try:
        if metadata["Name"] != "oshepherd":
            raise CandidateError("unexpected_distribution_name")
        version = Version(metadata["Version"])
        return version
    except (InvalidVersion, TypeError):
        raise CandidateError("invalid_distribution_version") from None


def verify_distributions(directory, *, source=ROOT, require_prerelease=False):
    directory, source = Path(directory), Path(source)
    wheels, sdists = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    entries = set(directory.iterdir())
    ignore = directory / ".gitignore"
    # uv creates this one-byte build-output marker. It is not a distribution and
    # is never uploaded/published; reject symlinks or any other hidden content.
    if (
        ignore in entries
        and not ignore.is_symlink()
        and ignore.is_file()
        and ignore.read_bytes() == b"*"
    ):
        entries.remove(ignore)
    if len(wheels) != 1 or len(sdists) != 1 or entries != set(wheels + sdists):
        raise CandidateError("exactly_one_wheel_and_sdist_required")
    if any(path.is_symlink() or not path.is_file() for path in wheels + sdists):
        raise CandidateError("distribution_must_be_regular_file")
    manifest_bytes = source.joinpath(MANIFEST).read_bytes()
    manifest = json.loads(manifest_bytes)
    expected = {MANIFEST: manifest_bytes}
    for name, digest in manifest["python_sources"].items():
        name = safe_name("oshepherd/" + name)
        expected[name] = source.joinpath(name).read_bytes()
        if sha256(expected[name]) != digest:
            raise CandidateError("source_manifest_hash_mismatch")
    actual_python = {
        str(path.relative_to(source))
        for path in source.joinpath("oshepherd").rglob("*.py")
        if "release" not in path.relative_to(source.joinpath("oshepherd")).parts
    }
    if actual_python != {name for name in expected if name.endswith(".py")}:
        raise CandidateError("source_manifest_inventory_mismatch")
    artifacts = {
        **manifest["artifacts"],
        **{item["path"]: item["sha256"] for item in manifest["migrations"]},
    }
    for name, digest in artifacts.items():
        name = safe_name("oshepherd/backends/supabase/release/" + name)
        expected[name] = source.joinpath(name).read_bytes()
        if sha256(expected[name]) != digest:
            raise CandidateError("source_manifest_hash_mismatch")
    project = tomllib.loads(source.joinpath("pyproject.toml").read_text())["project"]
    project_version = Version(project["version"])
    wheel, sdist = archive_files(wheels[0]), archive_files(sdists[0])
    metadata_names = [name for name in wheel if name.endswith(".dist-info/METADATA")]
    if len(metadata_names) != 1 or "PKG-INFO" not in sdist:
        raise CandidateError("missing_distribution_metadata")
    version = version_metadata(wheel[metadata_names[0]])
    if version != version_metadata(sdist["PKG-INFO"]) or version != project_version:
        raise CandidateError("distribution_version_mismatch")
    if require_prerelease and not (version.is_prerelease or version.is_devrelease):
        raise CandidateError("experimental_publish_requires_prerelease_version")
    if require_prerelease and version.local is not None:
        raise CandidateError("local_version_cannot_publish")
    # Profile qualification is reported verbatim, never inferred from packaging.
    profiles = manifest["profiles"]
    if any(not isinstance(value.get("qualified"), bool) for value in profiles.values()):
        raise CandidateError("invalid_profile_qualification")
    entrypoint_name = metadata_names[0].removesuffix("METADATA") + "entry_points.txt"
    parser = configparser.ConfigParser()
    parser.read_string(wheel.get(entrypoint_name, b"").decode())
    if (
        parser.get("console_scripts", "oshepherd", fallback=None)
        != "oshepherd.cli:main"
    ):
        raise CandidateError("missing_installed_console_entrypoint")
    for contents in (wheel, sdist):
        for name, content in expected.items():
            if contents.get(name) != content:
                raise CandidateError("distribution_source_or_asset_mismatch")
        # Packaging must not silently include an unmanifested executable or
        # private file inside the package, including stale build-directory files.
        package_files = {name for name in contents if name.startswith("oshepherd/")}
        if package_files != set(expected):
            raise CandidateError("unexpected_packaged_file")
    return {
        "version": str(version),
        "files": {path.name: sha256(path.read_bytes()) for path in wheels + sdists},
        "manifest_sha256": sha256(manifest_bytes),
        "profiles": {name: value["qualified"] for name, value in profiles.items()},
        "prerelease": version.is_prerelease or version.is_devrelease,
    }


def installed_checks(wheel, *, source=ROOT):
    # The exact wheel is resolved in a clean uv environment outside the checkout;
    # -I excludes Python path injection. Help commands do not touch providers,
    # Ollama, Redis, Docker, databases, or user configuration.
    code = r"""
import importlib.metadata, json, pathlib, subprocess
import oshepherd
from oshepherd.backends.supabase.releases import release_assets
assert "site-packages" in pathlib.Path(oshepherd.__file__).parts
release_assets()
checks=[]
for arguments in (["--help"], ["share","--help"], ["join","--help"], ["invite","--help"], ["service","--help"], ["supabase","--help"]):
    result=subprocess.run(["oshepherd",*arguments],capture_output=True,text=True,timeout=30)
    if result.returncode: raise SystemExit("installed_cli_help_failed")
    checks.append(" ".join(arguments))
print(json.dumps({"installed_version":importlib.metadata.version("oshepherd"),"installed_commands":checks}))
"""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    with tempfile.TemporaryDirectory(prefix="oshepherd-candidate-") as temporary:
        result = subprocess.run(
            [
                "uv",
                "run",
                "--isolated",
                "--no-project",
                "--no-config",
                "--python",
                "3.12",
                "--with",
                str(Path(wheel).resolve()) + "[supabase]",
                "--with-requirements",
                str(
                    Path(source)
                    .joinpath("templates/vercel/runtime-requirements.txt")
                    .resolve()
                ),
                "python",
                "-I",
                "-c",
                code,
            ],
            cwd=temporary,
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode:
            raise CandidateError("installed_candidate_checks_failed")
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise CandidateError("invalid_installed_check_result") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build with uv; destination must be absent or empty",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--expected-report",
        type=Path,
        help="Require the same source commit and exact artifact hashes as a prior verification",
    )
    parser.add_argument("--require-prerelease", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.build:
            if args.dist.exists() and any(args.dist.iterdir()):
                raise CandidateError("build_destination_must_be_empty")
            subprocess.run(
                ["uv", "build", "--no-sources", "--out-dir", str(args.dist.resolve())],
                cwd=ROOT,
                check=True,
                timeout=300,
            )
        report = verify_distributions(
            args.dist, require_prerelease=args.require_prerelease
        )
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        report["source_commit"] = commit
        if args.expected_report:
            previous = json.loads(args.expected_report.read_text())
            if any(
                previous.get(key) != report[key]
                for key in (
                    "files",
                    "source_commit",
                    "manifest_sha256",
                    "version",
                    "profiles",
                )
            ):
                raise CandidateError("candidate_report_mismatch")
        report.update(installed_checks(next(args.dist.glob("*.whl"))))
        if Version(report["installed_version"]) != Version(report["version"]):
            raise CandidateError("installed_version_mismatch")
        text = json.dumps(report, indent=2) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(text)
        print(text, end="")
    except (
        CandidateError,
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as error:
        parser.exit(
            1,
            "Candidate verification failed: "
            + (
                str(error)
                if isinstance(error, CandidateError)
                else "invalid_artifact_or_build"
            )
            + "\n",
        )


if __name__ == "__main__":
    main()
