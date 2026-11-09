# Installing COD2BOT

Two ways: add COD2BOT to an Eggdrop you already run (**A**), or build a new Eggdrop 1.10.2 bot
with COD2BOT included (**B**). Examples use `~/eggdrop` as the Eggdrop directory and `botuser` as the
Linux user; replace them with yours.

## 0. Requirements

| Requirement | Check | Install (Debian/Ubuntu) |
|---|---|---|
| Python ≥ 3.10 | `python3 -V` | `apt install python3` |
| Timezone data | `python3 -c "import zoneinfo; zoneinfo.ZoneInfo('Europe/Berlin')"` | `apt install tzdata` |
| curl (for `cod2botctl status`) | `curl --version` | `apt install curl` |
| tclsh (optional, Tcl test) | `echo 'puts [info patchlevel]' \| tclsh` | `apt install tcl` |
| Build tools (only for B) | `gcc --version` | `apt install build-essential tcl-dev libssl-dev` |

No `pip` packages are needed. The server needs outgoing **HTTPS** and **UDP** (CoD2 servers and the
master server `cod2master.activision.com:20710`).

**systemd user services** need a real login of the bot user (`ssh botuser@server`). With `su - botuser`
there is no user bus and the installers use crontab instead, which works just as well.
To keep user services running after logout and reboots (once, as root): `loginctl enable-linger botuser`.

---

## A. Add COD2BOT to an existing Eggdrop

```sh
cd ~/eggdrop
git clone https://github.com/sauk1337/cod2bot.git cod2bot
cd cod2bot
./install.sh                    # finds ../eggdrop.conf automatically
# or: ./install.sh --eggdrop /path/to/eggdrop [--no-systemd] [--no-check]
```

What it does (safe to run again, it never overwrites your settings):

1. Checks Python, tzdata and tclsh.
2. Creates `data/`, `logs/`, `backups/` (private), `.env` (600) and the shared token `data/api.token` (600).
3. Copies `eggdrop/scripts/cod2bot/cod2bot.tcl` to `<eggdrop>/scripts/cod2bot/`, creates `cod2bot.conf`
   there if it does not exist, and writes the absolute token path and the API port into it.
4. Adds `source scripts/cod2bot/cod2bot.tcl` to `eggdrop.conf` if missing (backup `eggdrop.conf.bak-<date>`).
5. Runs the tests.
6. Starts the backend: systemd user service `cod2bot-backend`, or crontab (`@reboot` + every 5 minutes).
7. Tests every data source live (`--check`, about 2 minutes).

Then on the partyline:

```
.rehash
.chanset #yourchannel +cod2
.cod2                        → backend: online v1.0.0 | commands: … | channels: #yourchannel
```

and in `#yourchannel`: `!ping`, `!cod2`, `!help`.

Finally set the announcement channels in `config/cod2bot.ini` (`[announcer] channels = #yourchannel`)
or from IRC with `!announce channels #yourchannel`.

---

## B. New bot: Eggdrop 1.10.2 + COD2BOT

```sh
# as root
apt update
apt install -y build-essential tcl tcl-dev libssl-dev python3 tzdata curl
loginctl enable-linger botuser

# as botuser
mkdir -p ~/eggdrop && cd ~/eggdrop
git clone https://github.com/sauk1337/cod2bot.git cod2bot
cp cod2bot/deploy/eggdrop.conf.example eggdrop.conf
nano eggdrop.conf          # every line marked CHANGE: nick, owner, net-type, server, channel, addresses
bash cod2bot/deploy/install-eggdrop.sh
```

`install-eggdrop.sh`:

1. Checks the build dependencies and prints the `apt install` line if something is missing.
2. Downloads `eggdrop-1.10.2.tar.gz` from ftp.eggheads.org and verifies its SHA-256
   (`d6646b630879e1acd9b053ffa12f46667f907145f29af1df0f82749ef3032a94`).
3. Builds it (`./configure`, `make config`, `make`) and installs it into the directory that contains
   `cod2bot/` (`make install` never overwrites an existing `eggdrop.conf`; an existing userfile is backed up).
4. Copies Eggdrop's stock scripts (`alltools.tcl`, `compat.tcl`, `autobotchk`…) without touching yours.
5. Warns if `vhost4`/`vhost6`/`listen-addr` are not addresses of this server.
6. If [service.tcl](https://github.com/sauk1337/service.tcl) is installed in `scripts/service/`, asks for the
   **Q account and password** and writes them into `scripts/service/service.conf` (automatic CHALLENGEAUTH
   login on every connect).
7. Installs the COD2BOT backend (`install.sh`).
8. Starts Eggdrop:
   - **with an existing userfile:** creates the watchdog (`autobotchk -systemd`, or `--cron`);
   - **without a userfile (new bot):** starts Eggdrop in creation mode (`-m`) and tells you what to do next.

### First start of a new bot

1. Wait until the bot is on IRC.
2. From IRC, with the nick written in `set owner` (on QuakeNet use `/mode yournick +x` first so the bot stores your stable host):
   ```
   /msg <botnick> hello
   /msg <botnick> pass <password>
   ```
3. Enable the watchdog (restarts the bot if it dies and after reboots):
   ```sh
   cd ~/eggdrop && ./scripts/autobotchk eggdrop.conf -10 -noemail
   ```
4. Partyline: `telnet <server> <listen port>` with your handle and password, then `.chanset #yourchannel +cod2` and `.cod2`.

### DCC CHAT

`/ctcp <botnick> chat` makes Eggdrop open a port for your client. The example config fixes the range with
`set reserved-portrange 3334:3340`; open it (and the `listen` port) in the firewall:

```sh
ufw allow 3333:3340/tcp
```

---

## Staff and channels

```
.+user <handle> *!*@<host>          add a user
.chattr <handle> +n                 owner
.chattr <handle> +m                 admin
.chattr <handle> |+o #yourchannel   moderator on that channel
.+chan #another                     join another channel
.chanset #another +cod2             enable COD2BOT there
.save
```

---

## Updating

```sh
cd ~/eggdrop/cod2bot
EGGDROP_DIR=~/eggdrop ./cod2botctl update     # backup, git pull, tests, copy cod2bot.tcl, restart backend
```
then `.rehash` on the partyline. Your `config/*.ini`, `data/` and `scripts/cod2bot/cod2bot.conf` are kept.

## Uninstalling

```sh
systemctl --user disable --now cod2bot-backend && rm ~/.config/systemd/user/cod2bot-backend.service
crontab -l | grep -v cod2botctl | crontab -       # if cron was used
```
Remove the line `source scripts/cod2bot/cod2bot.tcl` from `eggdrop.conf`, `.rehash`, and delete the
`cod2bot/` and `scripts/cod2bot/` folders.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `.cod2` says backend OFFLINE | `./cod2botctl status`, `./cod2botctl logs` |
| `backend refused the token (401)` in the Eggdrop log | another backend is running with an old token: `pkill -f "python3 -m app"`, `./cod2botctl start`, `.cod2 reload` |
| `systemctl --user`: Failed to connect to bus | you used `su`; log in directly via SSH, or keep using cron |
| No answer in a channel | the channel needs `+cod2` (`.chaninfo #channel`); the user may be ignored for flooding (60 s) |
| `CONFIG FILE NOT LOADED` … `scripts/…tcl: no such file` | the stock Eggdrop scripts are missing: re-run `install-eggdrop.sh` |
| Broken symbols (`Â»`) | `utf8_fix 1` in `cod2bot.conf`, or `ascii_only = yes` in `cod2bot.ini` |
| Times in UTC | install `tzdata`, restart the backend |
