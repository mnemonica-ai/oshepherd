# GitHub Pages: local build and preview

This internal guide is excluded from the published site. GitHub builds the site
from `main` at the repository root using its built-in Jekyll service. The local
recipe below keeps its Gemfile, downloaded gems, source snapshot, generated site,
and caches in a temporary directory. It adds no application dependency.

## Prepare Ruby and the temporary workspace

Prerequisites: Git, Python 3.9 or newer, and Ruby 3.3. The contract-test command
also requires `uv`. Replace `PAGES_REPO` below with your checkout path on other
machines.

Use Ruby 3.3 and Bundler 2.5.22. This was verified with Ruby 3.3.7,
`github-pages` 232, and Jekyll 3.10.0. This machine's system Ruby 2.6 cannot
satisfy the current Nokogiri requirement; Ruby 4 cannot satisfy the Pages
CommonMarker dependency. A supported Ruby 3.3 installation must already be
available before running these commands.

On this machine, an existing Homebrew portable Ruby works without installing
another Ruby. This path is optional and specific to this machine; otherwise
activate your own Ruby 3.3 installation:

```sh
export PATH="/opt/homebrew/Library/Homebrew/vendor/portable-ruby/3.3.7/bin:$PATH"
ruby --version
```

Run the following in a shell. The fixed workspace path can be reused in
another terminal. `/private/tmp` and `pwd -P` matter on macOS because
Jekyll safe mode checks real paths when loading custom includes. Using the
`/tmp` symlink as the source path can silently select the theme's empty include
instead of this repository's custom CSS and link script.

```sh
export PAGES_REPO="/Users/raulpino/repos/mia/oshepherd"
export PAGES_WORK="/private/tmp/oshepherd-pages-local"
mkdir -p "$PAGES_WORK"
export PAGES_WORK="$(cd "$PAGES_WORK" && pwd -P)"
export GEM_HOME="$PAGES_WORK/gems"
export GEM_PATH="$GEM_HOME"
export BUNDLE_GEMFILE="$PAGES_WORK/Gemfile"
export BUNDLE_APP_CONFIG="$PAGES_WORK/bundle-config"
export BUNDLE_PATH="$PAGES_WORK/bundle"
export JEKYLL_ENV=production
export PATH="$GEM_HOME/bin:$PATH"
printf 'Workspace: %s\n' "$PAGES_WORK"
cd "$PAGES_WORK"

cat > "$BUNDLE_GEMFILE" <<'GEMFILE'
source "https://rubygems.org"
gem "github-pages", "~> 232"
gem "rake"
GEMFILE

gem install bundler -v 2.5.22 --no-document --install-dir "$GEM_HOME"
bundle _2.5.22_ install
```

The initial install and each remote-theme download require network access.
The generated scratch `Gemfile.lock` records the resolved dependency versions.
`rake` supports the remote theme's dependency loading in the local verifier.

## Snapshot and build

The snapshot copies the **current working contents** of tracked paths, including
unstaged edits. New files must first be staged explicitly with `git add path`
so `git ls-files` includes them. Staging does not commit them. Ignored files,
local environments, and untracked handoff files never enter the snapshot.

Run this complete block for the first build and after every source change.
It replaces only the scratch source and generated site. Run builds from the
scratch workspace so Sass caches stay outside the repository.

```sh
cd "$PAGES_WORK"
python3 - <<'PY'
import os
from pathlib import Path
import shutil
import subprocess

repo = Path(os.environ["PAGES_REPO"]).resolve()
work = Path(os.environ["PAGES_WORK"]).resolve()
source = work / "source"
destination = work / "public" / "oshepherd"
for directory in (source, destination):
    if directory.exists():
        shutil.rmtree(directory)
source.mkdir()
paths = subprocess.check_output(["git", "-C", str(repo), "ls-files", "-z"])
for name in paths.decode().split("\0"):
    if not name:
        continue
    original = repo / name
    if original.is_file():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
PY

bundle _2.5.22_ exec github-pages build \
  --source "$PAGES_WORK/source" \
  --destination "$PAGES_WORK/public/oshepherd"
```

Use `github-pages build`: its entrypoint activates GitHub's default plugins and
safe mode. A plain `jekyll build` does not reproduce that configuration. Keep
`JEKYLL_ENV=production` set. `_config.yml` removes the original Markdown files
through both `readme_index.remove_originals` and
`optional_front_matter.remove_originals`.

## Start and stop the preview

Serve the parent of the generated `oshepherd` directory so the configured
`/oshepherd` base URL works. This command runs in the foreground:

```sh
cd "$PAGES_WORK"
python3 -m http.server 4000 --bind 127.0.0.1 --directory "$PAGES_WORK/public"
```

Open <http://127.0.0.1:4000/oshepherd/>. Stop the server with **Ctrl+C** in
that terminal. To stop a server left running in another terminal, identify its
process first, then send the normal termination signal to its PID:

```sh
lsof -nP -iTCP:4000 -sTCP:LISTEN
# Replace 12345 with the Python preview server PID shown above.
kill 12345
```

If port 4000 belongs to another service, leave that process running and choose a
different port in the Python command and preview URL.

## Resume, rebuild, and restart later

In a new terminal, activate Ruby 3.3 again and restore the environment below.
Keep the same environment for every subsequent build or dependency command.

```sh
export PATH="/opt/homebrew/Library/Homebrew/vendor/portable-ruby/3.3.7/bin:$PATH"
export PAGES_REPO="/Users/raulpino/repos/mia/oshepherd"
export PAGES_WORK="/private/tmp/oshepherd-pages-local"
export PAGES_WORK="$(cd "$PAGES_WORK" && pwd -P)"
export GEM_HOME="$PAGES_WORK/gems"
export GEM_PATH="$GEM_HOME"
export BUNDLE_GEMFILE="$PAGES_WORK/Gemfile"
export BUNDLE_APP_CONFIG="$PAGES_WORK/bundle-config"
export BUNDLE_PATH="$PAGES_WORK/bundle"
export JEKYLL_ENV=production
export PATH="$GEM_HOME/bin:$PATH"
cd "$PAGES_WORK"
bundle _2.5.22_ check
```

To restart an unchanged preview, run the Python server command above. To preview
edits, rerun the entire snapshot/build block, then reload the browser. The
server can stay running during a rebuild; requests may briefly fail while the
generated directory is replaced. If the temporary workspace has been deleted,
repeat setup.

## Verify the build

List the actual output and check that every file is an allowed HTML page or
asset. Search records must point only to published pages; their text can
legitimately mention plans or protocol documents.

```sh
cd "$PAGES_WORK"
find "$PAGES_WORK/public/oshepherd" -type f | sort
python3 - <<'PY'
import json
import os
from pathlib import Path

site = Path(os.environ["PAGES_WORK"]) / "public" / "oshepherd"
files = [p.relative_to(site) for p in site.rglob("*") if p.is_file()]
assert files, "No generated files"
unexpected = [str(p) for p in files if not (
    str(p) == "index.html" or p.parts[0] == "assets" or
    (len(p.parts) == 2 and p.parts[0] == "docs" and p.suffix == ".html")
)]
assert not unexpected, unexpected
search = json.loads((site / "assets/js/search-data.json").read_text())
for record in search.values():
    url = record["url"]
    assert url.startswith("/oshepherd/"), url
    target = url.removeprefix("/oshepherd/").split("#", 1)[0]
    if not target or target.endswith("/"):
        target += "index.html"
    assert target == "index.html" or (
        target.startswith("docs/") and target.count("/") == 1
        and target.endswith(".html")
    ), url
    assert (site / target).is_file(), url
home = (site / "index.html").read_text()
assert "DOMContentLoaded" in home, "Custom link script missing"
assert "--shepherd" in home, "Custom CSS missing"
assert home.count('class="language-mermaid"') == 2
assert "mermaid@12.3.0" in home
worker = (site / "docs/supabase-worker-walkthrough.html").read_text()
assert 'id="watch-worker-logs"' in worker
print(f"Verified {len(files)} files and {len(search)} search records")
PY

cd "$PAGES_REPO"
uv run --locked pytest -q tests/contracts
```

The initial verified build contained 24 files: ten HTML pages and fourteen
assets/theme files. Check in a browser that both Mermaid diagrams render,
search finds guides, navigation works, and the worker log anchor scrolls to
its heading. Plans, protocol, `.py`, migrations-directory, and `LICENSE` links
should open the corresponding GitHub source. Published guide links should
remain on the preview site. These browser checks need network access for the
Mermaid CDN.

## Remove the local preview workspace

Stop the Python server first. Then remove only the temporary workspace created
by this recipe:

```sh
cd "$PAGES_REPO"
case "$PAGES_WORK" in
  /private/tmp/oshepherd-pages-local) rm -rf -- "$PAGES_WORK" ;;
  *) printf 'Refusing unexpected workspace path: %s\n' "$PAGES_WORK" ;;
esac
```

## Publish on GitHub

Confirm the repository is public if using free GitHub Pages. In repository
**Settings → Pages**, choose **Deploy from a branch**, branch **main**, folder
**/(root)**. Publishing requires pushing the desired commits to GitHub; local
builds and local merges alone do not publish. The public URL is
<https://mnemonica-ai.github.io/oshepherd/>. No custom workflow or repository
Gemfile is needed.
