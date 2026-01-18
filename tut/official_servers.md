# Official Servers

This document explains how to run an official server that can update Steam stats.

## Requirements

- A Steam app ID.
- A Steam publisher key (server-only secret).
- Server host with outbound HTTPS access to `partner.steam-api.com`.

## Configuration (recommended: environment variables)

Set these on the server host before starting the server:

- `OFFICIAL_SERVER=1`
- `STEAM_APP_ID=<your_app_id>`
- `STEAM_PUBLISHER_KEY=<your_publisher_key>`

### Linux examples

One-off (current shell only):

```bash
export OFFICIAL_SERVER=1
export STEAM_APP_ID=123456
export STEAM_PUBLISHER_KEY=your_publisher_key_here
./run.sh
```

Persist for your user (add to `~/.bashrc` or `~/.profile`):

```bash
export OFFICIAL_SERVER=1
export STEAM_APP_ID=123456
export STEAM_PUBLISHER_KEY=your_publisher_key_here
```

Then reload:

```bash
source ~/.bashrc
```

## Configuration (alternate: config.txt)

If you prefer using `config.txt`, add:

- `server_settings.official_server 1`
- `server_settings.steam_app_id <your_app_id>`
- `server_settings.steam_publisher_key <your_publisher_key>`

## How it works

- The server checks `official_server` and only submits Steam stats when enabled.
- Steam stats are updated via the Steam Web API `ISteamUserStats/SetUserStatsForGame`.
- Achievement unlocks are handled by Steam automatically based on stat thresholds.

## Security notes

- Never commit `STEAM_PUBLISHER_KEY` to the repo.
- Do not distribute it to clients.
- Use environment variables or a secrets manager on the server host.

## Troubleshooting

- If stats do not update, verify:
  - `OFFICIAL_SERVER=1`
  - valid `STEAM_APP_ID` and `STEAM_PUBLISHER_KEY`
  - outbound HTTPS access to `partner.steam-api.com`
- Check server logs for `Steam stats` warnings.
