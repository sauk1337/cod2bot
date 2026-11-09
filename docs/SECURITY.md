# Security

## COD2BOT measures

| Risk | Measure |
|---|---|
| Running system commands from IRC | There is no path to it: no `exec` or `eval` of user input, neither in Tcl nor in Python |
| Exposed internal API | Listens on `127.0.0.1` only, rejects non-loopback clients, 48-hex token (mode 600) compared in constant time, max. 8 KB body |
| SQL injection | Always parameterised queries; dynamic columns only from whitelists |
| IRC input | Validation of commands (`^[a-z0-9_-]{1,32}$`), tags, countries, URLs (`http(s)` + length), `ip:port` addresses, dates; external text stripped of control characters |
| Scanning private networks through `!server` | Public IPs only (loopback, private, link-local, multicast and reserved are rejected) |
| HTTP requests | Timeouts, max. size 3 MB, `http(s)` only, robots.txt (RFC 9309), at least 2 s per host, ETag/If-Modified-Since |
| Malicious XML | Documents with `<!ENTITY>` are rejected; expat does not resolve external entities |
| Flood | Tcl: 5 commands/30 s per host (then ignored for 60 s), 8 replies/30 s per channel; backend: second limiter; announcements: minimum interval and hourly cap per channel; lines ≤ 400 bytes |
| Impersonation in registrations | Clans, players and wars tied to the Q account (+x) or the handle; only the owner or a moderator edits |
| Privileges | Levels from Eggdrop flags (`n`, `m`, `o`); the backend trusts only that level, and only the Tcl side knows the token to send it |
| Credentials | Token in `data/api.token`/`.env` (600, outside git); no user passwords are stored: identity is the Q account |
| Auditing | Every administrative action in `admin_logs` and `logs/admin.log` |
| Errors | No exception reaches the user (generic message + traceback in the log); jobs back off |

## Recommendations for the Eggdrop around it

- Do not load debug scripts that run Tcl or shell commands from a channel (for example `!tcl` / `!exec`
  binds of `tcl.tcl`) on a public bot: a stolen owner hostmask becomes a shell on your server.
- On QuakeNet, log the bot in to Q with CHALLENGEAUTH (e.g. [service.tcl](https://github.com/sauk1337/service.tcl))
  instead of plain `AUTH` in a script, and keep that config file at mode 600.
- Use PBKDF2 for the userfile (`loadmodule pbkdf2`); keep `blowfish` loaded only while migrating old hashes.
- Open only the partyline `listen` port and the DCC range (`reserved-portrange`) in the firewall.
