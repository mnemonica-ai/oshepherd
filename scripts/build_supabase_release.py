"""Refresh packaged release assets; never regenerate an existing SQL migration.

The initial migration is a reviewed snapshot, independent of the local spike.
Run with uv run --locked python -m scripts.build_supabase_release.
"""

import hashlib
import json
from pathlib import Path
import shutil
from oshepherd.backends.supabase.releases import SCHEDULES

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "oshepherd/backends/supabase/release"


def refresh():
    ASSETS.mkdir(parents=True, exist_ok=True)
    entries = []
    for source in sorted((ROOT / "supabase/migrations").glob("*.sql")):
        data = source.read_bytes()
        body, record = data.split(b"\n-- Release record.", 1)
        content_digest = hashlib.sha256(body).hexdigest()
        if content_digest.encode() not in record:
            raise ValueError("Migration content hash does not match its release record")
        target = ASSETS / "migrations" / source.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        entries.append(
            {
                "version": source.name.split("_", 1)[0],
                "path": "migrations/" + source.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_sha256": content_digest,
            }
        )
    artifacts = {}
    for source in sorted((ROOT / "supabase/functions/ollama").glob("*.ts")):
        if source.name.endswith("_test.ts"):
            continue
        name = "functions/ollama/" + source.name
        target = ASSETS / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        artifacts[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {
        "v": 1,
        "release": "supabase-v1-local-candidate",
        "protocol": 1,
        "schema": "oshepherd_api",
        "private_schema": "oshepherd",
        "bucket": "oshepherd-results",
        "python": "3.12",
        "supabase_cli": "2.116.0",
        "gateway": "ollama",
        "profiles": {
            "edge_free": {"sync_seconds": 135, "qualified": False},
            "vercel_hobby": {
                "sync_seconds": 135,
                "response_bytes": 4000000,
                "qualified": False,
            },
        },
        "migrations": entries,
        "artifacts": artifacts,
        "python_sources": {
            str(source.relative_to(ROOT / "oshepherd")): hashlib.sha256(
                source.read_bytes()
            ).hexdigest()
            for source in sorted((ROOT / "oshepherd").rglob("*.py"))
            if "release" not in source.relative_to(ROOT / "oshepherd").parts
        },
        "schedules": [
            {"name": name, "interval": interval, "command": command}
            for name, (interval, command) in SCHEDULES.items()
        ],
    }
    for source in sorted((ROOT / "templates/vercel").iterdir()):
        if not source.is_file():
            continue
        name = "templates/vercel/" + source.name
        target = ASSETS / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest["artifacts"][name] = hashlib.sha256(source.read_bytes()).hexdigest()
    (ASSETS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    refresh()
