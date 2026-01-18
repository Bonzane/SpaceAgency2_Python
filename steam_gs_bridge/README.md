# Steam GS Unlock Helper

This helper unlocks GS-authoritative achievements using the Steamworks GameServer SDK.

Build:

```bash
make
```

Run:

```bash
./steam_gs_unlock --steamid 7656119... --achievement ACH_NAME --app-id 123456
```

Optional arguments:
- `--game-port`, `--query-port`
- `--product`, `--game-desc`, `--mod-dir`, `--server-name`
- `--version`, `--server-mode`, `--timeout-ms`

Notes:
- Requires `steamworks_sdk_163/` extracted at repo root (or set `STEAM_SDK`).
- Uses anonymous gameserver logon; official server IPs must be configured in Steamworks.
