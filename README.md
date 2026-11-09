# COD2BOT

**A Call of Duty 2 community bot for Eggdrop.**
News, tournaments, ladders, live game servers, clans, players and clan wars of the CoD2 scene,
straight into your IRC channel, with clean mIRC colours and without ever blocking your Eggdrop.

```
[COD2][NEWS]    FPSChallenge » VirtualGamerz takes the crown at CoD2 SA - MIXCUP! · results · https://fpschallenge.eu/news/…
[COD2][TOURNEY] CoD2 SA - Nightcup #13 · registration open · 3/16 teams · 5v5 · Start: Mon 09/11 00:01 CET
[COD2][LADDER]  5vs5 S&D EU · #1 NEWERA 3404  #2 Team MiCRO 3189  #3 E V I L5 3163 …
[COD2][SERVER]  cw1 · ONLINE · Clan War #1 · Map: mp_toujane SD · 8/12 ▰▰▰▰▰▱▱▱ · 32ms · v1.3
[COD2][WAR]     #12 ABC vs XYZ · 5v5 · Sat 15/11 21:00 CET · in 2h 10m · Maps: Toujane, Burgundy
[COD2][PLAYER]  sniper · Clan War #1 · Map: mp_carentan · 203.0.113.10:28960
```

- **Eggdrop side:** one Tcl script, asynchronous, no extra Tcl packages.
- **Backend:** Python 3.9+ using **only the standard library** (no `pip install`), SQLite storage.
- **Real data sources only**, checked against their `robots.txt` and terms (see [docs/SOURCES.md](docs/SOURCES.md)).

---

## Contents

- [Features](#features)
- [What the bot does by itself](#what-the-bot-does-by-itself)
- [Command guide](#command-guide)
- [Permissions](#permissions)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Operation](#operation)
- [Adding news sources](#adding-news-sources)
- [Colours and look](#colours-and-look)
- [Security](#security)
- [Tests](#tests)
- [Project layout](#project-layout)
- [Limitations](#limitations)
- [License](#license)

---

## Features

| Module | What it does |
|---|---|
| **News** | Polls verified CoD2 sources (FPSChallenge, zPAM and CoD2x releases, ModDB), detects new items and content changes, removes duplicates across sources, classifies them (results, tournaments, LAN, ladders, tools, mods, maps…), keeps images, and posts new items automatically. RSS/Atom feeds can be added from IRC. |
| **Competitive** | CoD2 tournaments from FPSChallenge: state, registration window, check-in, map pool, entry (free/paid), registered teams. Team profiles with ladder **rank, ELO and W-L**. Winners taken from the official results news. Manual tournaments and results for anything else. |
| **Servers** | Live queries of any CoD2 server (Quake III protocol over UDP): hostname with CoD colours, map, game type, players and scores, slots, ping, version, password, PunkBuster. Favourite servers are monitored (down / back online announcements, 24 h availability). A **master server browser** scans every public server to answer `!servers top`, `!server find` and `!find <player>`. |
| **Community** | Player and clan registry tied to the user's **QuakeNet Q account** (`+x` host) or Eggdrop handle, so nobody can edit someone else's profile. Clan pages with members and war record. |
| **Wars** | Schedule clan wars (format, maps, server), automatic reminders before the start, results, history and head-to-head. |
| **CoD2 knowledge** | Competitive map pool, 19 maps, 27 weapons by faction, game modes, classes, network/FPS/mouse configs, console and rcon commands, mods, server tools, guides, tips, quotes and facts — all editable in one JSON file. |
| **Announcer** | Central queue for automatic messages with priorities, de-duplication and per-channel anti-flood (minimum interval and hourly cap). |
| **Administration** | Levels from Eggdrop flags, modules on/off at runtime, broadcast, maintenance mode, backups, action log, job status. |

## What the bot does by itself

| Event | Default |
|---|---|
| New news item from a source with `announce = yes` | posted in the announce channels (max. 2 per source per check, only items < 48 h old) |
| CoD2 tournament announced / registration opens / goes live | posted |
| Tournament starts in ≤ 60 min | reminder posted |
| Clan war created / result entered | posted in the other announce channels |
| Clan war starts in 60 and 15 min | reminders in the channel where it was created + announce channels |
| Favourite server stops answering (3 checks) / comes back | posted |
| Daily digest (news of the day, open and live tournaments, wars in the next 24 h) | 20:00 |
| Database backup, cleanup of old data, integrity check | daily at 05:00 |

Anti-flood: at most **one automatic message every 90 s** and **12 per hour** per channel (configurable).
The first fetch of a source is silent, so installing the bot never floods the channel with old news.

---

## Command guide

Default prefix `!`. Commands work in channels with the Eggdrop flag `+cod2` and in private messages
(`/msg <botnick> !news`). `!cod2 <command>` always reaches COD2BOT even if another script uses the same name.
Long listings are sent by NOTICE; the channel gets at most 4 lines per command.

### General

| Command | Description |
|---|---|
| `!cod2` | Scene summary: news, active tournaments, upcoming wars, servers and players online |
| `!cod2 <command> [args]` | Run any COD2BOT command |
| `!help` | Categories (by notice) |
| `!help <category>` | Commands of a category: `general news competitive servers community cod2 fun admin` |
| `!help <command>` | Description, syntax, aliases and required level |
| `!about` · `!version` · `!uptime` · `!ping` | Bot information |
| `!stats` | Statistics (staff also see HTTP counters, announcement queue and failing jobs) |
| `!modules` | Module states |
| `!time` · `!date` | Time and date in the configured timezone |

### News

| Command | Description |
|---|---|
| `!news` | Latest 3 items |
| `!news latest` | Most recent item with its summary |
| `!news <N>` | Latest N items (up to 10; more than 4 are sent by notice) |
| `!news <source> [page]` | Items of one source, e.g. `!news fpschallenge`, `!news zpam` |
| `!news search <text>` | Search titles and summaries |
| `!news sources` | Sources and their state (● ok · ● with errors · ○ disabled) |
| `!news categories` · `!news category <name>` | Categories with counts · items of a category |
| `!news check [source]` | *(moderator)* Check sources now |
| `!news publish [#id]` | *(moderator)* Post an item again |
| `!news addsource <url> [name]` | *(admin)* Add an RSS/Atom feed (downloaded and validated first) |
| `!news removesource <name>` · `enable` · `disable` | *(admin)* Manage sources |

### Competitive

| Command | Description |
|---|---|
| `!tournaments [upcoming\|live\|finished] [page]` | CoD2 tournaments |
| `!tournament <name\|#id>` | State, teams, format, start, registration end, check-in, entry, map pool, link |
| `!teams [tournament]` | Registered teams (default: the active tournament with most teams) |
| `!team <name\|tag>` | Country, members, matches, ladder rank / ELO / W-L, roster |
| `!ladders` | Known CoD2 ladders and how many teams are known in each |
| `!standings [league] [page]` | Standings by rank/ELO (*partial*: teams seen in tournaments) |
| `!results` | Tournament winners, manual results and played wars |
| `!matches` | Scheduled wars and live tournaments |
| `!tournament add <dd/mm> <hh:mm> <name> [url]` | *(moderator)* Manual tournament |
| `!tournament winner <#id> <team>` · `!tournament del <#id>` | *(moderator)* Set winner · delete a manual tournament |
| `!results add <team1> <s1>-<s2> <team2> [map]` · `!results del <#id>` | *(moderator)* Manual results |

### Servers

| Command | Description |
|---|---|
| `!servers [online\|page]` | Favourite servers with their last state |
| `!servers top` | Busiest public CoD2 servers right now |
| `!server <name\|ip:port>` | Live status (any public server, saved or not) |
| `!server players <name>` | Players and scores |
| `!server map <name>` | Current map and game type |
| `!server status <name>` | Status and availability over the last 24 h |
| `!server find <text>` | Search online servers by name |
| `!find <player>` | Which public server a player is on right now (alias `!whereis`) |
| `!server add <name> <ip:port>` · `!server remove <name>` | *(moderator)* Favourites — public IPs only |

### Community: players and clans

Registering needs an identity: authed to Q with `+x` (`/mode yournick +x`) or a handle on the bot.

| Command | Description |
|---|---|
| `!player register [country]` | Register your nick (linked to your Q account) |
| `!player set country\|role\|description <value>` | Edit your profile |
| `!player <nick>` · `!profile <nick>` | Profile: clan, country, role, ✔ Q, and whether they are playing right now |
| `!player del <nick>` | *(moderator)* |
| `!clans [page]` | All clans |
| `!clan <tag>` | Clan page: name, country, members, war record, IRC channel, website, contact |
| `!clan register <tag> <name>` | Register a clan (max. 3 per account); you become its owner |
| `!clan set <tag> name\|website\|irc\|country\|description\|contact <value>` | *(clan owner / moderator)* |
| `!clan add <tag> <nick>` · `!clan kick <tag> <nick>` | *(clan owner / moderator)* Members (registered players) |
| `!clan del <tag>` | *(clan owner / moderator)* |

### Clan wars

| Command | Description |
|---|---|
| `!war create <clan1> <clan2> <date> <time> [format] [maps] [server=<name>]` | e.g. `!war create ABC XYZ 15/11 21:00 5v5 toujane,burgundy server=cw1` |
| `!war upcoming` | Upcoming wars |
| `!war list` | Upcoming wars and recent results |
| `!war info <#id>` | One war |
| `!war result <#id> <s1>-<s2>` | *(creator / moderator)* Enter the result (announced) |
| `!war cancel <#id>` | *(creator / moderator)* |
| `!war history <clan>` | Record and last results |
| `!war <clan1> <clan2>` | Head to head |

Dates: `15/11`, `15/11/2026`, `2026-11-15`, `today`, `tomorrow`. Times: `21:00`, `21h`.
Map names are recognised (`toujane` → *Toujane*). Formats: `1v1` … `9v9`.

### Call of Duty 2 information

| Command | Description |
|---|---|
| `!maps [all]` | Competitive map pool · all maps |
| `!map <name>` | Map card (`toujane`, `mp_toujane`, `Railyard MJR`…) |
| `!weapons [faction\|class]` | Weapons by faction (`american british russian german`) or class (`rifle smg sniper`…) |
| `!weapon <name>` | Weapon card (`kar98k`, `thompson`, `mp44`…) |
| `!gamemodes` · `!classes` | Game modes · weapon roles |
| `!configs [network\|fps\|mouse\|hud\|performance\|competitive]` | Recommended settings |
| `!commands [connect\|record\|screenshot\|bind\|vstr\|name\|rcon\|exec]` | Console and rcon commands |
| `!mods [topic]` · `!tools [topic]` · `!guides [topic]` | Mods (zPAM, CoD2x…), server tools, short guides |
| `!tips` | Random tip (`!tips add <text>` moderator) |

### Fun

| Command | Description |
|---|---|
| `!randommap [all]` · `!randomweapon` | Random pick |
| `!quote` · `!fact` | Random quote · CoD2 fact (`add` for moderators) |

### Administration

| Command | Level | Description |
|---|---|---|
| `!admin` | moderator | Staff command list |
| `!reload` | admin | Reload the `.ini` files, news sources and knowledge base |
| `!module enable\|disable <module>` | admin | `news tournaments servers cod2 clans players wars announcer fun` |
| `!announce` | admin | Announcement channels, enabled types, queue size |
| `!announce channels #a #b` | admin | Set the announcement channels |
| `!announce <type> on\|off` | admin | `news tournaments wars servers_down daily_digest` |
| `!broadcast <message>` | admin | Message to every announcement channel (highest priority) |
| `!backup` | admin | Database backup now |
| `!adminlog [n]` | admin | Last administrative actions |
| `!jobs` · `!jobs run <name>` | admin | Background jobs and their state · run one now |
| `!maintenance on\|off` | owner | Only staff can use the bot |

### Partyline

| Command | Description |
|---|---|
| `.cod2` | Script status: backend online/offline, version, number of commands, active channels |
| `.cod2 reload` | Re-read the API token and the command list |
| `.chanset #channel +cod2` · `-cod2` | Enable / disable COD2BOT on a channel |

---

## Permissions

Levels come from Eggdrop flags (configurable in `scripts/cod2bot/cod2bot.conf`):

| Level | Default flag | Grant it with |
|---|---|---|
| 0 user | — | — |
| 1 registered | any handle on the bot | `.adduser <nick>` |
| 2 moderator | `o` global or channel | `.chattr <handle> +o` or `.chattr <handle> \|+o #channel` |
| 3 admin | `m` | `.chattr <handle> +m` |
| 4 owner | `n` | `.chattr <handle> +n` |

Ownership of clans and wars is tied to the Q account (or handle) of whoever registered them.

---

## Architecture

```
IRC ── Eggdrop ── scripts/cod2bot/cod2bot.tcl ──── HTTP on 127.0.0.1 (async, token) ────┐
                                                                                        ▼
                                                              backend/ (Python 3, standard library)
                                                               ├─ news ........ FPSChallenge API · GitHub API · RSS/Atom · HTML
                                                               ├─ tournaments . FPSChallenge API (tournaments, teams, ladder)
                                                               ├─ servers ..... Quake III UDP protocol + Activision master server
                                                               ├─ community ... players, clans, wars
                                                               ├─ cod2 ........ knowledge base (data/seed/cod2.json)
                                                               ├─ announcer ... announcement queue with anti-flood
                                                               └─ SQLite (WAL, migrations, daily backups)
```

Eggdrop runs Tcl in a single thread, so a slow website inside a bind would freeze the bot. The Tcl
script only parses commands, computes the user level and makes **asynchronous** HTTP calls to the
local backend; everything slow happens in the backend's own threads. If the backend stops, the bot
stays on IRC, answers "backend unavailable" and recovers by itself. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Requirements

- **Eggdrop 1.8 or newer** (tested design target: 1.9 / 1.10) with Tcl 8.5+ (8.6 recommended)
- **Python 3.9+** and the system timezone database (`tzdata`)
- Outgoing HTTPS (news, FPSChallenge, GitHub) and UDP (CoD2 servers, master `cod2master.activision.com:20710`)
- Linux with systemd user services or cron (any POSIX shell for `cod2botctl`)
- Optional: `curl` (for `cod2botctl status`), `tclsh` (runs the Tcl integration test)

---

## Installation

### A. On an existing Eggdrop

```sh
cd /path/to/eggdrop
git clone https://github.com/sauk1337/cod2bot.git cod2bot
cd cod2bot
./install.sh                     # or: ./install.sh --eggdrop /path/to/eggdrop
```

`install.sh` (safe to run again; it never overwrites your settings):

1. checks Python, tzdata and tclsh;
2. creates `data/`, `logs/`, `backups/`, `.env` and the shared API token `data/api.token` (mode 600);
3. copies `eggdrop/scripts/cod2bot/` into `<eggdrop>/scripts/cod2bot/` (keeps an existing `cod2bot.conf`) and writes the absolute token path and API port into it;
4. adds `source scripts/cod2bot/cod2bot.tcl` to `eggdrop.conf` if missing (with a backup);
5. runs the test suite;
6. starts the backend as a **systemd user service** (`cod2bot-backend`) or, without systemd, via crontab (`@reboot` + watchdog every 5 min);
7. tests every data source live (`--check`, ~2 min).

Then, on the partyline:

```
.rehash
.chanset #yourchannel +cod2
.cod2
```

and in the channel: `!cod2`, `!help`.

To keep the systemd user service running without a login session (once, as root): `loginctl enable-linger <user>`.
Log in directly as the bot user (SSH): `su - user` sessions have no systemd user bus, and the installer falls back to cron.

### B. New bot with Eggdrop 1.10.2 built from source

```sh
# as root
apt install -y build-essential tcl tcl-dev libssl-dev python3 tzdata curl
# as the bot user
mkdir -p ~/eggdrop && cd ~/eggdrop
git clone https://github.com/sauk1337/cod2bot.git cod2bot
cp cod2bot/deploy/eggdrop.conf.example eggdrop.conf
nano eggdrop.conf                 # nick, owner, server, channel, addresses
bash cod2bot/deploy/install-eggdrop.sh
```

`install-eggdrop.sh` downloads Eggdrop 1.10.2 from ftp.eggheads.org, verifies its SHA-256, builds and
installs it next to your `eggdrop.conf` (never overwriting it), installs COD2BOT and starts the bot.
Without a userfile it starts Eggdrop in creation mode (`-m`): from IRC send `/msg <botnick> hello`
with the nick set in `set owner`, then `/msg <botnick> pass <password>`, and enable the watchdog with
`./scripts/autobotchk eggdrop.conf -10 -noemail`. If [service.tcl](https://github.com/sauk1337/service.tcl)
is present it also asks for the Q account to log in automatically.

Full guide: [INSTALL.md](INSTALL.md).

---

## Configuration

| File | What |
|---|---|
| `config/cod2bot.ini` | Backend: timezone, prefix, announcement channels and limits, intervals, modules, colours, rate limits, database retention, backups |
| `config/news_sources.ini` | News sources |
| `<eggdrop>/scripts/cod2bot/cod2bot.conf` | Eggdrop side: API address, token file, prefix, flags → levels, anti-flood, channels enabled at load |
| `data/seed/cod2.json` | Maps, weapons, configs, guides, tips, quotes (reloaded with `!reload`) |
| `.env` | Secrets (`COD2BOT_API_TOKEN`, optional — by default the token lives in `data/api.token`) |

The most common settings:

```ini
; config/cod2bot.ini
[general]
timezone = Europe/Berlin
timezone_label = CET
prefix = !

[announcer]
channels = #yourchannel        ; channels that receive automatic posts (they also need +cod2)
min_interval = 90              ; seconds between two automatic messages in a channel
max_per_hour = 12
news = yes
tournaments = yes
wars = yes
servers_down = yes
daily_digest = yes
digest_hour = 20
war_reminders = 60,15

[tournaments]
games = Call of Duty 2         ; FPSChallenge game names, comma separated

[irc]
max_channel_lines = 4
colors = yes
ascii_only = no                ; yes if your network mangles UTF-8
```

```tcl
# scripts/cod2bot/cod2bot.conf
variable enable_chans {#yourchannel}   ;# channels that get +cod2 automatically
variable private 1                     ;# commands by /msg
variable chanop_is_moderator 0         ;# channel @ without a handle = moderator
variable user_max 5                    ;# commands per user ...
variable user_window 30                ;# ... per 30 seconds (then ignored 60 s)
```

Runtime changes from IRC (`!module`, `!announce`, `!maintenance`) are stored in the database and take
precedence over the `.ini`. Every option: [docs/CONFIG.md](docs/CONFIG.md).

---

## Operation

```sh
cd /path/to/eggdrop/cod2bot
./cod2botctl status            # backend state + API health
./cod2botctl start|stop|restart
./cod2botctl logs [module]     # backend, core, api, news, tournaments, servers, community, cod2, admin, scheduler
./cod2botctl check             # test every data source now
./cod2botctl say "!tournaments"   # run a command without IRC
./cod2botctl backup            # also automatic every day
./cod2botctl restore backups/cod2bot-YYYYMMDD-HHMMSS.db
./cod2botctl test
EGGDROP_DIR=/path/to/eggdrop ./cod2botctl update   # backup, git pull, tests, restart; then .rehash
```

More: [docs/OPERATIONS.md](docs/OPERATIONS.md).

---

## Adding news sources

From IRC (RSS/Atom):

```
!news addsource https://example.org/feed.xml example
```

Or in `config/news_sources.ini` (then `!reload`):

```ini
[example]
type = feed               ; fpschallenge_api | github_releases | rss | atom | feed | html
label = Example
url = https://example.org/feed.xml
interval = 1800
announce = yes
category = community
colour = 12
```

GitHub releases (`type = github_releases`, `url = https://api.github.com/repos/<owner>/<repo>/releases?per_page=10`)
and plain HTML pages with a regular expression (`type = html`, `item_pattern = …`) are supported too.
The bot refuses to fetch anything `robots.txt` disallows. Details and the list of verified sources:
[docs/SOURCES.md](docs/SOURCES.md).

---

## Colours and look

Every line starts with a two-tone badge: `COD2` (black on yellow) plus the section in its own colour —
NEWS green, TOURNEY maroon, RESULT lime, LADDER maroon, SERVER blue, WAR red, CLAN purple, PLAYER teal,
MAP orange, WEAPON silver, INFO grey, ADMIN yellow on black. Labels are grey, values bold, states
green/red, links blue, and CoD colour codes in server and player names (`^1`, `^3`…) are converted
to mIRC colours. Channels with mode `+c` (or listed in `nocolor_chans`) receive plain text automatically.
All colours can be changed in the `[theme]` section of `config/cod2bot.ini`.

---

## Security

- No path from IRC to the system shell: no `exec`/`eval` of user input in Tcl or Python.
- The backend listens on **127.0.0.1 only**, rejects non-loopback clients and requires a 48-hex token
  (file mode 600) compared in constant time.
- Parameterised SQL everywhere; strict validation of commands, tags, URLs, addresses and dates.
- `!server` only queries public IP addresses (no scanning of private networks).
- HTTP with timeouts, size limits, per-host rate limit and an RFC 9309 `robots.txt` matcher.
- Two layers of rate limiting (Tcl and backend) plus per-channel anti-flood for announcements.
- Every administrative action is logged.

More: [docs/SECURITY.md](docs/SECURITY.md).

---

## Tests

```sh
cd backend
python3 -m unittest discover -s tests
```

54 tests: IRC formatting and byte-safe truncation, robots.txt rules, the CoD2 query protocol (including
master packets whose IP bytes contain `\`), all parsers with real captured responses, database
migrations/backups, every module with an isolated database and a fake HTTP client, the HTTP API, and
an integration test that loads `cod2bot.tcl` in a simulated Eggdrop (`tclsh`) against the real backend.

---

## Project layout

```
cod2bot/
├── backend/
│   ├── app/
│   │   ├── __main__.py        entry point (server, --check, --backup, --say)
│   │   ├── bootstrap.py       wiring, scheduled maintenance, daily digest
│   │   ├── api/               local HTTP API for the Eggdrop script
│   │   ├── core/              config, database, HTTP client, IRC formatting, router, scheduler, announcer, help/admin
│   │   ├── news/              parsers + news service and commands
│   │   ├── tournaments/       FPSChallenge tournaments, teams, ladders, results
│   │   ├── servers/           CoD2 server protocol, monitor, master browser
│   │   ├── community/         players, clans, wars
│   │   └── cod2/              knowledge base commands
│   └── tests/                 unit + integration tests, real fixtures, Eggdrop simulator
├── config/                    cod2bot.ini, news_sources.ini
├── data/seed/cod2.json        CoD2 knowledge base
├── eggdrop/scripts/cod2bot/   cod2bot.tcl + cod2bot.conf (copied into Eggdrop by install.sh)
├── deploy/                    systemd units, eggdrop.conf.example, install-eggdrop.sh
├── docs/                      architecture, commands, config, sources, operations, security, limitations
├── install.sh                 installer
├── cod2botctl                 control script
└── .env.example
```

---

## Limitations

- FPSChallenge has no public API for match lists or complete standings: `!standings` is built from the
  teams seen in CoD2 tournaments and is labelled as partial; `!matches` shows community wars and live tournaments.
- The FPSChallenge website itself is behind a Cloudflare bot challenge; the bot uses only its public JSON API.
- Server browser data (`!find`, `!servers top`) is refreshed every 10 minutes.
- Bot messages are in English (add a catalogue in `backend/app/core/i18n.py` for another language).

Full list: [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

---

## License

MIT — see [LICENSE](LICENSE). Data shown by the bot belongs to its sources (FPSChallenge, GitHub
projects, ModDB); the bot only posts headlines and links back to them.
