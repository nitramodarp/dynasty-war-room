# Dynasty weekly newsletter ("Only Offensive Matters")

Runs every Tuesday via GitHub Actions (`.github/workflows/newsletter.yml`), or on demand from the Actions tab (Run workflow).

Pipeline: `fetch_sleeper.py` → `generate_newsletter.py` → `generate_meme.py` → `render.py`.

- Newest edition, always: `newsletter/output/latest.html`
- Each week's page: `newsletter/output/2026-wkNN.html`
- Secrets needed (repo Settings → Secrets → Actions): `ANTHROPIC_API_KEY`, `IMGFLIP_USERNAME`, `IMGFLIP_PASSWORD`
- League id, owner map and commissioner live in `newsletter/scripts/config.py`
- Separate from the redraft newsletter: own state files, own workflow, nothing shared.
