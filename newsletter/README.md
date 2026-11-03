# Dynasty weekly newsletter ("Only Offensive Matters")

Run it by hand every Tuesday morning: Actions tab → "Dynasty Weekly Newsletter" → Run workflow (no schedule — GitHub delayed scheduled runs by hours).

Pipeline: `fetch_sleeper.py` → `generate_newsletter.py` → `generate_meme.py` → `render.py`.

- Newest edition, always: `newsletter/output/latest.html`
- Each week's page: `newsletter/output/2026-wkNN.html`
- Secrets needed (repo Settings → Secrets → Actions): `ANTHROPIC_API_KEY`, `IMGFLIP_USERNAME`, `IMGFLIP_PASSWORD`
- League id, owner map and commissioner live in `newsletter/scripts/config.py`
- Separate from the redraft newsletter: own state files, own workflow, nothing shared.
