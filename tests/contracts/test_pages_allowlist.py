"""Keep the branch-based Pages build limited to public documentation."""

from pathlib import Path, PurePosixPath
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]


def repository_files():
    try:
        return subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=ROOT, text=True
        ).split("\0")[:-1]
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Source archives have no Git index. Ignore local hidden tool directories.
        return [
            path.relative_to(ROOT).as_posix()
            for entry in ROOT.iterdir()
            if not entry.name.startswith(".")
            for path in (entry.rglob("*") if entry.is_dir() else [entry])
            if path.is_file()
        ]


def test_pages_publishes_only_allowlisted_files():
    config = yaml.safe_load((ROOT / "_config.yml").read_text())
    assert config["readme_index"]["remove_originals"] is True
    assert config["optional_front_matter"]["remove_originals"] is True
    excluded = {value.rstrip("/") for value in config["exclude"]}
    assert "docs/plans" in excluded
    unexpected = []
    for name in repository_files():
        path = PurePosixPath(name)
        if any(str(parent) in excluded for parent in (path, *path.parents)):
            continue
        if name in {"README.md", "_config.yml"}:
            continue
        if path.parts[0] == "_includes":
            continue
        if len(path.parts) == 2 and not path.name.startswith("."):
            if path.parts[0] == "docs" and path.suffix == ".md":
                continue
            if path.parts[0] == "assets" and path.suffix.lower() in {
                ".png",
                ".svg",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".ico",
                ".avif",
            }:
                continue
        unexpected.append(name)
    assert not unexpected, f"Add explicit Pages exclusions for: {unexpected}"
