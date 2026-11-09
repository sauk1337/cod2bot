# Configuration

| What | Where |
|---|---|
| IRC connection: nick, altnick, ident, realname, server, port, SSL, owner | `eggdrop.conf` (Eggdrop owns the IRC connection) |
| Q login, channel protection (optional [service.tcl](https://github.com/sauk1337/service.tcl)) | `scripts/service/service.conf` |
| Prefix, permissions, IRC anti-flood, active channels, API | `scripts/cod2bot/cod2bot.conf` |
| Timezone, intervals, announcements, modules, limits, colours | `cod2bot/config/cod2bot.ini` |
| News sources | `cod2bot/config/news_sources.ini` |
| Secrets | `cod2bot/.env`, `cod2bot/data/api.token` (mode 600) |
| CoD2 data (maps, weapons, guides...) | `cod2bot/data/seed/cod2.json` (reloaded with `!reload`) |

## `scripts/cod2bot/cod2bot.conf`

| Variable | Default | Meaning |
|---|---|---|
| `api` | `http://127.0.0.1:8765` | Backend (same port as `[api] port`) |
| `tokenfile` | absolute path (install.sh) | Shared token |
| `prefix` | `!` | Prefix (same as `[general] prefix`) |
| `enable_chans` | `{}` | Channels that get `+cod2` when the script loads |
| `private` | 1 | Allow commands by `/msg` |
| `flag_owner/admin/moderator` | `n` / `m` / `o\|o` | Mapping from Eggdrop flags to levels |
| `chanop_is_moderator` | 0 | Treat channel ops (@) without a handle as moderators |
| `user_max` / `user_window` | 5 / 30 | Commands per user and window (s); above it the user is ignored for `ignore_time` s |
| `chan_max` / `chan_window` | 8 / 30 | Replies per channel; the rest goes by notice |
| `max_chan_lines` / `max_notice_lines` | 5 / 12 | Safety caps |
| `nocolor_chans` | `{}` | Channels without colours (`+c` channels are detected automatically) |
| `poll` | 15 | Seconds between announcement polls |
| `timeout` | 15000 | HTTP timeout (ms) |
| `yield_to_service` | 1 | If a user's service.tcl trigger is `!`, commands both scripts have (`help`, `version`) are answered by service.tcl |
| `reserved` | `tcl exec error` | Never sent to the backend (commands of debug scripts such as tcl.tcl) |
| `utf8_fix` | 0 | Set to 1 only if you see broken characters |

## `cod2bot/config/cod2bot.ini`

Every key is commented in the file itself. The most used ones:

- `[general] timezone = Europe/Berlin` · `timezone_label = CET` · `prefix = !` · `language = en`
- `[announcer] channels = #yourchannel` · `min_interval = 90` · `max_per_hour = 12` · `news/tournaments/wars/servers_down/daily_digest = yes|no` · `digest_hour = 20` · `war_reminders = 60,15`
- `[news] default_interval` · `announce_max_age_hours` · `announce_per_run`
- `[tournaments] interval = 900` · `games = Call of Duty 2` · `fetch_teams = yes` · `announce_registration` · `announce_start_minutes`
- `[servers] check_interval = 300` · `down_after = 3` · `timeout = 2.0` · `master_cache = 600`
- `[irc] max_line_bytes = 400` · `max_channel_lines = 4` · `colors = yes` · `ascii_only = no`
- `[theme]` colours of the badge, labels, links, states (mIRC numbers 0-15)
- `[modules]` enable/disable modules (also at runtime with `!module`)
- `[ratelimit]` backend limits (an extra layer on top of the Tcl one)
- `[database]` days to keep news, server history, logs and announcements
- `[backup] hour = 5` · `keep = 14`

Runtime changes from IRC (stored in the `bot_config` table, they take precedence over the `.ini`):
`!module enable|disable`, `!announce channels ...`, `!announce <type> on|off`, `!maintenance on|off`.
After editing the `.ini` files: `!reload` (or `./cod2botctl restart` for `[api]`/`[logging]`).
