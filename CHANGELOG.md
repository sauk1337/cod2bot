# Changelog

## 1.0.0 — 2026-11-09

First public release.

- Eggdrop script (`cod2bot.tcl`): asynchronous HTTP to a local backend, levels from Eggdrop flags,
  per-user and per-channel anti-flood, `+cod2` channel flag, private commands, `+c` channel detection,
  coexistence with service.tcl, partyline `.cod2`.
- Python backend (standard library only) with SQLite, migrations, daily backups and cleanup.
- News: FPSChallenge API, GitHub releases (zPAM, CoD2x), ModDB RSS; RSS/Atom/HTML sources;
  de-duplication, change detection, categories, automatic posting; RFC 9309 robots.txt matcher.
- Competitive: FPSChallenge CoD2 tournaments, teams, ladder rank/ELO/W-L, winners from results news,
  manual tournaments and results.
- Servers: CoD2 query protocol, favourites with down/up monitoring and history, master server browser
  (`!servers top`, `!server find`, `!find <player>`).
- Community: players and clans tied to the Q account, clan wars with reminders, results and history.
- CoD2 knowledge base: maps (2026 competitive pool), weapons, modes, configs, commands, mods, guides.
- Announcer with priorities, de-duplication and per-channel limits; daily digest.
- Installers: `install.sh` (existing Eggdrop) and `deploy/install-eggdrop.sh` (Eggdrop 1.10.2 from source).
- 54 tests (CI on Python 3.10 and 3.13) including a simulated-Eggdrop integration test.
