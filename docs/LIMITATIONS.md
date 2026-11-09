# Known limitations

## External data

- **FPSChallenge** has no public API for *matches* or *full standings*. Therefore:
  - `!standings` is built from the teams seen in CoD2 tournaments (their profiles include rank/ELO/W-L for
    each ladder). It is **partial** and the bot always says so. Today: 41 teams in 5vs5 S&D EU.
  - `!matches` shows community wars and live tournaments; FPSChallenge matches are not available.
  - Winners come from the official results news ("X takes the crown at Y", "X conquers Y", "X wins Y").
    If FPSChallenge changes that wording, `!tournament winner` sets it by hand.
  - The HTML site (which has ladders and matches) is behind Cloudflare's bot challenge. With the site's
    permission, its admin can allow your server's IP addresses in Cloudflare;
    then HTML parsers for those pages can be added.
- **FPSChallenge's timezone** is assumed to be Central European (`provider_timezone`), deduced from its ISO
  dates carrying `+02:00` in summer. Adjust it if that changes.
- The FPSChallenge API is undocumented: if it changes, the job fails with backoff, `!tournaments` warns that
  the data may be outdated and the news from other sources keeps working. Parsers validate the format.
- **Reddit** is disabled (robots.txt forbids it).
- **GitHub API**: 60 requests/hour per IP without a token (the bot uses 2/hour with ETag).
- FPSChallenge news/tournament links (`/news/{slug}`, `/tournament/{slug}`) follow the pattern indexed by
  search engines; they could not be opened from here because of the Cloudflare challenge for non-browser clients.
- Teams have no link: the team URL pattern could not be verified.

## Servers

- Quake 3 protocol over UDP (`getstatus`); ping is the round trip from the VPS, not the players' ping.
  Servers that filter UDP or rate-limit queries may show as OFFLINE.
- `!find`/`!servers top`/`!server find` use the master scan (every 10 min): up to 10 minutes of delay.
  Only servers registered on the Activision master.
- The VPS queries over IPv4 (the master returns IPv4).

## Integration

- The Tcl script was tested in a **simulated** Eggdrop with `tclsh` 8.6 (23 checks) and together with the real
  service.tcl v6.1.0; **not on the live QuakeNet bot** from here (the binary is Linux and this environment
  is Windows). Recommended first test: `.cod2` on the partyline and `!ping` / `!map toujane` in your channel.
- It relies on Eggdrop servicing the Tcl event loop for asynchronous HTTP requests (Eggdrop 1.8+ does;
  1.10.2 included).
- `deploy/install-eggdrop.sh` (building Eggdrop 1.10.2 on the VPS) could not be run here: there is no Linux
  build environment. Its steps are Eggdrop's standard `configure / make config / make / make install`, plus
  `autobotchk`; the generated `eggdrop.conf` was syntax-checked with tclsh.
- UTF-8: if the network/client shows `»`, `·` or `▰` wrongly, use `utf8_fix 1` or `ascii_only = yes`.
- If a user's service.tcl trigger is `!`, `!help`/`!version` (present in both scripts) are answered by
  service.tcl; `!cod2 help` always reaches COD2BOT.
- Bot messages are English only; another language can be added as a catalogue in `app/core/i18n.py`.

## Knowledge data

- The CoD2 knowledge base (`data/seed/cod2.json`) is curated content: the competitive pool comes from real
  FPSChallenge tournaments (November 2026); configs are limited to well-known cvars and remind users to check
  each league's rules. Extend it by editing the JSON (`!reload`).
