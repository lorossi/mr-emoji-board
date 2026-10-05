# ⛙ MR Emoji Board

Turns the emoji reactions in the `#tools-reviews` Mattermost channel into a review board,
and cross-checks each MR against GitLab.

| Reaction | State |
|---|---|
| (none) | 🙋 Needs review |
| :eyes: | 👀 In review |
| :pencil: (`memo`) | 📝 Back to author |
| :white_check_mark: | ✅ Approved, needs merge |
| :merged: | ⛙ Merged |
| :x: / :no_entry_sign: | Closed |

The last of 📝/✅ wins, so a 📝 after a ✅ sends the MR back to the author.
GitLab decides merged/closed; mismatches show up under "🧹 Forgotten updates".

## Secrets

Put each token in its own file (both are git-ignored):

```bash
mkdir -p secrets && chmod 700 secrets
echo '<MMAUTHTOKEN cookie value>' > secrets/mm_token       # Mattermost session cookie
glab config get token --host gitlab.cern.ch > secrets/gitlab_token
chmod 600 secrets/*
```

The app reads each secret from `/run/secrets/<name>` (Docker), then `secrets/<name>` (local dev),
then the `$NAME` environment variable (e.g. `MM_TOKEN`).
The MMAUTHTOKEN cookie expires with your Mattermost session; refresh the file when harvests start failing.

## Run

```bash
docker compose up -d --build        # http://localhost:8000 (PORT=... to change)
```

On Linux, Compose bind-mounts secret files with their host owner, so the container user
(`board`) must be able to read them (e.g. `chmod 644`, or match the UID).

Local dev without Docker (reads the same `secrets/` files):

```bash
uv sync
uv run uvicorn mr_board.main:app --reload
uv run mr-board-harvest -o data.json   # one-shot dump
uv run pytest
```

Settings (`MM_URL`, `MM_CHANNEL_ID`, `MM_CHANNEL_NAME`, `GITLAB_URL`, `HARVEST_DAYS`,
`REFRESH_MINUTES`, `MERGED_DAYS`) live in [`settings.env`](settings.env). An environment variable with the same
name overrides the file, and `MR_BOARD_CONFIG=/path/to/other.env` points at a different file.
