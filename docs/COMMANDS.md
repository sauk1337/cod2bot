# COD2BOT commands

Default prefix `!` (configurable). Commands work in channels with `+cod2` and in private
(`/msg <botnick> !news`). `!cod2 <command>` runs any command even if another script uses the same name.

## Levels

| Level | Who | How to get it |
|---|---|---|
| 0 user | anyone | — |
| 1 registered | has a handle in the Eggdrop userfile | `.adduser` / service.tcl `adduser` |
| 2 moderator | global or channel flag `o` | `.chattr nick +o` / `.chattr nick |+o #yourchannel` |
| 3 admin | global flag `m` | `.chattr nick +m` |
| 4 owner | global flag `n` | `.chattr nick +n` |

Registering **clans, players and wars** requires an identity: being authed with Q with **+x**
(host `account.users.quakenet.org`) or having a handle on the bot. The Q account gives ownership:
only its owner (or a moderator) can edit a clan or the result of a war.

## General
| Command | Description |
|---|---|
| `!cod2` | Scene summary: news, active tournaments, wars, servers/players online |
| `!cod2 <command> [args]` | Runs any bot command |
| `!help [category\|command]` | Help by notice (categories: general news competitive servers community cod2 fun admin) |
| `!about` `!version` `!uptime` `!ping` | Bot information |
| `!stats` | Statistics (moderators also see HTTP, announcement queue and jobs) |
| `!modules` | Active modules |
| `!time` `!date` | Time and date (configured timezone, CET by default) |

## News
| Command | Description |
|---|---|
| `!news` | Latest 3 news |
| `!news latest` | The most recent one with its summary |
| `!news 5` | The latest 5 (more than 4 → by notice) |
| `!news fpschallenge` `!news zpam` ... `[page]` | By source |
| `!news search <text>` | Search titles and summaries |
| `!news sources` | Sources (● ok, ● orange with errors, ○ disabled) |
| `!news categories` / `!news category <name>` | Categories and the news of one category |
| `!news check [source]` | (mod) Check sources now |
| `!news publish [#id]` | (mod) Announce a news item again |
| `!news addsource <url> [name]` | (admin) Add an RSS/Atom feed (validated by downloading it) |
| `!news removesource <name>` / `enable` / `disable` | (admin) Manage sources |

## Competitive
| Command | Description |
|---|---|
| `!tournaments [upcoming\|live\|finished\|page]` | CoD2 tournaments (FPSChallenge + manual) |
| `!tournament <name\|#id>` | Details: state, teams, format, start, registration end, check-in, entry, map pool, link |
| `!teams [tournament]` | Registered teams (by default the active tournament with most teams) |
| `!team <name\|tag>` | Profile: country, members, matches, ladder (rank, ELO, W-L), roster |
| `!ladders` | Known CoD2 ladders (5vs5 S&D EU, Mixed S&D EU, 2vs2 S&D EU, SA...) |
| `!standings [league] [page]` | Standings by ELO (**partial**, see limitations) |
| `!results` | Tournament winners, recent matches and wars |
| `!matches` | Scheduled wars and live tournaments |
| `!tournament add <dd/mm> <hh:mm> <name> [url]` | (mod) Manual tournament |
| `!tournament winner <#id> <team>` / `del <#id>` | (mod) Set winner / delete a manual one |
| `!results add <team1> <s1>-<s2> <team2> [map]` / `del <#id>` | (mod) Manual result |

## Servers
| Command | Description |
|---|---|
| `!servers [page\|online]` | Favourites with their state |
| `!servers top` | Busiest public servers (master scan every 10-15 min) |
| `!server <name\|ip:port>` | Live status: name (with CoD colours), map, mode, players, ping, version, password, PB |
| `!server players <name>` | Players and scores |
| `!server map <name>` | Current map and mode |
| `!server status <name>` | Status + availability over the last 24 h |
| `!server find <text>` | Find online servers by name |
| `!find <player>` | Which public server is this player on right now? |
| `!server add <name> <ip:port>` / `remove <name>` | (mod) Favourites (public IPs only) |

## Community
| Command | Description |
|---|---|
| `!player register [country]` | Register your nick (needs Q +x or a handle) |
| `!player set country\|role\|description <value>` | Edit your profile |
| `!player <nick>` / `!profile <nick>` | Profile (clan, country, role, ✔ Q, and whether they are playing now) |
| `!clans` | List of clans |
| `!clan register <tag> <name>` | Register a clan (max. 3 per account) |
| `!clan set <tag> name\|website\|irc\|country\|description\|contact <value>` | (owner/mod) Edit |
| `!clan add\|kick <tag> <nick>` | (owner/mod) Members (registered players) |
| `!clan <tag>` | Profile: members, wars W-L-D, IRC, website |
| `!clan del <tag>` | (owner/mod) |

## Wars
| Command | Description |
|---|---|
| `!war create <clan1> <clan2> <date> <time> [5v5] [map1,map2] [server=name]` | Date: `15/11`, `15/11/2026`, `2026-11-15`, `today`, `tomorrow`; time `21:00` or `21h` |
| `!war upcoming` / `!war list` | Upcoming wars (list also shows recent results) |
| `!war info <#id>` | Details |
| `!war result <#id> <s1>-<s2>` | (creator/mod) Result → announcement |
| `!war cancel <#id>` | (creator/mod) |
| `!war history <clan>` | History and record |
| `!war <clan1> <clan2>` | Head to head |

Automatic reminders 60 and 15 minutes before (configurable) in the channel where the war was created and in the announcement channels.

## CoD2 information
`!maps [all]` · `!map <name>` (accepts `toujane`, `mp_toujane`, `Railyard MJR`...) · `!weapons [faction|class]` ·
`!weapon <name>` · `!classes` · `!gamemodes` · `!configs [network|fps|mouse|hud|performance|competitive]` ·
`!commands [connect|record|screenshot|bind|vstr|name|rcon|exec]` · `!mods [topic]` · `!tools [topic]` ·
`!guides [topic]` · `!tips` (`!tips add <text>` mod) · `!quote` / `!fact` (`add` mod) · `!randommap [all]` · `!randomweapon`

## Administration
| Command | Level | Description |
|---|---|---|
| `!admin` | mod | List of staff commands |
| `!reload` | admin | Reload the `.ini`, sources and knowledge base |
| `!module enable\|disable <news\|tournaments\|servers\|cod2\|clans\|players\|wars\|announcer\|fun>` | admin | At runtime |
| `!broadcast <message>` | admin | To every announcement channel (highest priority) |
| `!announce [status]` / `channels #a #b` / `<type> on\|off` | admin | Types: news tournaments wars servers_down daily_digest |
| `!backup` | admin | Back up the database now |
| `!adminlog [n]` | admin | Last administrative actions |
| `!jobs` / `!jobs run <name>` | admin | Background jobs |
| `!maintenance on\|off` | owner | Only staff can use the bot |

Partyline: `.cod2` (status) and `.cod2 reload` (re-reads the token and the command list).
