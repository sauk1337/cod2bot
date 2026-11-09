# Day-to-day operation

Everything from the `cod2bot/` folder inside your Eggdrop directory:

| Action | Command |
|---|---|
| Start the backend | `./cod2botctl start` (or `systemctl --user start cod2bot-backend`) |
| Stop | `./cod2botctl stop` |
| Restart | `./cod2botctl restart` |
| Status + API health | `./cod2botctl status` |
| Live logs | `./cod2botctl logs` · `./cod2botctl logs news` (core, api, news, tournaments, servers, community, cod2, admin, scheduler) |
| Test every source | `./cod2botctl check` |
| Try a command without IRC | `./cod2botctl say "!tournaments"` |
| Tests | `./cod2botctl test` |
| Backup now | `./cod2botctl backup` (or `!backup` on IRC) |
| Restore | `./cod2botctl restore backups/cod2bot-YYYYMMDD-HHMMSS.db` |
| Update | `EGGDROP_DIR=/path/to/eggdrop ./cod2botctl update`, then `.rehash` on the partyline |

Eggdrop: as before (`./eggdrop eggdrop.conf`, `.restart`, `.rehash`, `.die`), or with
`deploy/systemd/eggdrop.service`.

## Automatic restart and recovery

- Backend: systemd `Restart=always` (or crontab every 5 min when there are no systemd user services).
- If the backend goes down, the Tcl script detects it, answers "backend unavailable" (max. 1 notice/min) and
  recovers on its own when it is back; no `.rehash` needed.
- IRC disconnections: handled by Eggdrop; on reconnect (`init-server`) the command list is refreshed.
- Broken sources: exponential backoff per source (max. 12 h; HTTP 429 → at least 1 h). `!news sources` shows it.

## Backups and cleanup

- Every day at 05:00 (`[backup] hour`): consistent online SQLite copy into `backups/` (14 are kept),
  cleanup of old data and an integrity check (a failure is written to `logs/core.log`).
- For off-site copies, sync `backups/` (e.g. `rsync` from another machine).

## Logs

`logs/backend.log` (everything) plus one per module; they rotate at 5 MB keeping 5 copies (`[logging]`).
Administrative actions also go to the `admin_logs` table (`!adminlog`). Eggdrop keeps `logs/eggdrop.log`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `.cod2` says backend OFFLINE | `./cod2botctl status`; check `logs/backend.log`; the `[api]` port must match `api` in `cod2bot.conf` |
| "backend refused the token (401)" in the Eggdrop log | `tokenfile` in `cod2bot.conf` must point to `cod2bot/data/api.token`; `.cod2 reload` |
| The bot does not answer in a channel | does it have `+cod2`? `.chaninfo #channel`; is the user ignored for flooding? (60 s) |
| Broken symbols (`Â»`) | `utf8_fix 1` in `cod2bot.conf`, or `ascii_only = yes` in the `.ini` |
| Times shown in UTC | install `tzdata` and restart the backend |
| `!help` shows the service.tcl help | that user has trigger `!` in service.tcl and both scripts have the command: service.tcl wins on purpose; use `!cod2 help` |
