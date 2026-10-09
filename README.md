# ⛙ MR Emoji Board

Turns the emoji reactions in the `#tools-reviews` Mattermost channel into a review board,
and cross-checks each MR against GitLab.

| Reaction | State |
|---|---|
| (none) | 🙋 Needs review |
| :eyes: | 👀 In review |
| :pencil: (`memo`) | 📝 Back to author |
| :white_check_mark: | ✅ Approved |
| :merged: | ⛙ Merged |
| :x: / :no_entry_sign: | Closed |

The last of 📝/✅ wins, so a 📝 after a ✅ sends the MR back to the author.
GitLab decides merged/closed; mismatches (including a ✅ that disagrees with the GitLab
approvals, either way) show up under "🧹 Forgotten updates".

## Secrets

Put each secret in its own file (all git-ignored):

```bash
mkdir -p secrets && chmod 700 secrets
echo '<client secret>' > secrets/mm_client_secret   # the board's Mattermost OAuth app
glab config get token --host gitlab.cern.ch > secrets/gitlab_token
chmod 600 secrets/*
```

The app reads each secret from `/run/secrets/<name>`.

## Mattermost access (OAuth 2.0)

The board reads Mattermost through an OAuth 2.0 app registered under
*Integrations → OAuth 2.0 Applications*, whose callback URL is `http://localhost:8000/oauth/callback`.
Its client ID and callback URL are in [`settings.env`](settings.env) (`MM_OAUTH_CLIENT_ID`,
`MM_OAUTH_REDIRECT_URL`, which must match the app exactly); the client secret is `secrets/mm_client_secret`.

Connect once after the first start: open <http://localhost:8000/oauth/login> (or follow the
"Connect to Mattermost" link on the board) and log in with CERN SSO. The board reads the channel
as the account that connected it. Its tokens are kept in `STATE_DIR` (the `state` volume in Docker)
and renewed automatically; connect again only if the board says Mattermost refused to renew them.

## MR database

Harvested MR posts are kept in SQLite (`board.db` in `STATE_DIR`). The first harvest reads the
last `HARVEST_DAYS` of the channel; after that each harvest reads only posts since the previous
one, and re-reads every known MR post that isn't finished yet (`/posts/<id>/thread`) for its
current emojis and replies. An MR is finished, and no longer re-read, once its emoji says merged
or closed and GitLab agrees. Open MRs stay on the board however old they are; finished ones
for `HARVEST_DAYS`. Deleting `board.db` starts over from a full scan.

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

Settings (`MM_URL`, `MM_CHANNEL_ID`, `MM_CHANNEL_NAME`, `MM_OAUTH_CLIENT_ID`, `MM_OAUTH_REDIRECT_URL`,
`GITLAB_URL`, `HARVEST_DAYS`, `REFRESH_MINUTES`, `MERGED_DAYS`, `STATE_DIR`) live in [`settings.env`](settings.env). An environment variable with the same
name overrides the file, and `MR_BOARD_CONFIG=/path/to/other.env` points at a different file.
