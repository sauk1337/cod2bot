#!/usr/bin/env bash
# Builds and installs Eggdrop 1.10.2 into the bot directory (the folder that contains cod2bot/),
# keeping an existing eggdrop.conf, userfile, chanfile and scripts,
# then installs the COD2BOT backend and starts everything.
#
#   cod2bot/deploy/install-eggdrop.sh [--dest /path/to/eggdrop] [--no-start] [--cron]
#
# Run it as the bot's Linux user, NOT as root. Safe to run again.
set -euo pipefail

VERSION="1.10.2"
SHA256="d6646b630879e1acd9b053ffa12f46667f907145f29af1df0f82749ef3032a94"
URL="https://ftp.eggheads.org/pub/eggdrop/source/1.10/eggdrop-${VERSION}.tar.gz"

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DEST="$(cd "$HERE/../.." && pwd)"      # bundle layout: <DEST>/cod2bot/deploy/
START=1
SUPERVISOR="systemd"
while [ $# -gt 0 ]; do
    case "$1" in
        --dest) DEST="$2"; shift 2 ;;
        --no-start) START=0; shift ;;
        --cron) SUPERVISOR="cron"; shift ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "unknown option $1"; exit 1 ;;
    esac
done

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;35m !!\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERR\033[0m %s\n' "$*"; exit 1; }

[ "$(id -u)" -ne 0 ] || die "do not run this as root: log in as the bot user (ssh youruser@host) and run it again"
[ -f "$DEST/eggdrop.conf" ] || die "$DEST/eggdrop.conf not found: copy cod2bot/deploy/eggdrop.conf.example to $DEST/eggdrop.conf and edit it"
conf_value() { sed -n "s/^set $1 \"\([^\"]*\)\".*/\1/p" "$DEST/eggdrop.conf" | head -1; }
USERFILE="$(conf_value userfile)"; USERFILE="${USERFILE:-bot.user}"
CHANFILE="$(conf_value chanfile)"; CHANFILE="${CHANFILE:-bot.chan}"
PIDF="$(conf_value pidfile)"; PIDF="${PIDF:-pid.bot}"
[ -f "$DEST/$USERFILE" ] || say "no $USERFILE: fresh install, the owner is created at the first start (/msg <bot> hello)"
ok "bot directory: $DEST"

# --- 1. build dependencies ----------------------------------------------------
say "Checking build dependencies"
missing=()
command -v gcc >/dev/null || missing+=(build-essential)
command -v make >/dev/null || missing+=(make)
command -v curl >/dev/null || missing+=(curl)
command -v tclsh >/dev/null || missing+=(tcl)
TCLCONF="$(ls /usr/lib/tcl8.6/tclConfig.sh /usr/lib/*/tcl8.6/tclConfig.sh /usr/lib64/tclConfig.sh 2>/dev/null | head -1 || true)"
[ -n "$TCLCONF" ] || missing+=(tcl-dev)
[ -f /usr/include/openssl/ssl.h ] || missing+=(libssl-dev)
command -v python3 >/dev/null || missing+=(python3)
python3 -c 'import zoneinfo; zoneinfo.ZoneInfo("Europe/Berlin")' 2>/dev/null || missing+=(tzdata)
# optional Tcl packages used by some third-party scripts (e.g. service.tcl's updater needs tls)
echo 'package require tls' | tclsh >/dev/null 2>&1 || warn "optional: Tcl tls package not installed (apt install tcl-tls)"
if [ ${#missing[@]} -gt 0 ]; then
    die "missing packages. Run as root, then run this script again:
      apt update && apt install -y ${missing[*]}"
fi
ok "build dependencies present"

# --- 2. download + verify ----------------------------------------------------------
BUILD="$HOME/eggdrop-build"
mkdir -p "$BUILD"
cd "$BUILD"
if [ ! -f "eggdrop-${VERSION}.tar.gz" ]; then
    say "Downloading Eggdrop ${VERSION}"
    curl -fsSL -o "eggdrop-${VERSION}.tar.gz" "$URL"
fi
echo "${SHA256}  eggdrop-${VERSION}.tar.gz" | sha256sum -c - >/dev/null \
    || { rm -f "eggdrop-${VERSION}.tar.gz"; die "SHA-256 mismatch - download removed, try again"; }
ok "eggdrop-${VERSION}.tar.gz verified (sha256)"

# --- 3. build + install ---------------------------------------------------------------
say "Building Eggdrop ${VERSION} (a few minutes)"
rm -rf "eggdrop-${VERSION}"
tar xzf "eggdrop-${VERSION}.tar.gz"
cd "eggdrop-${VERSION}"
./configure >"$BUILD/configure.log" 2>&1 || die "configure failed - see $BUILD/configure.log"
make config >"$BUILD/make-config.log" 2>&1 || die "make config failed - see $BUILD/make-config.log"
make -j"$(nproc 2>/dev/null || echo 2)" >"$BUILD/make.log" 2>&1 || die "make failed - see $BUILD/make.log"

# stop a running bot before replacing the binary
if [ -f "$DEST/$PIDF" ] && kill -0 "$(cat "$DEST/$PIDF")" 2>/dev/null; then
    say "Stopping the running Eggdrop (pid $(cat "$DEST/$PIDF"))"
    kill "$(cat "$DEST/$PIDF")"; sleep 3
fi
cp "$DEST/eggdrop.conf" "$DEST/eggdrop.conf.bak-$(date +%Y%m%d%H%M%S)"
[ -f "$DEST/$USERFILE" ] && cp "$DEST/$USERFILE" "$DEST/$USERFILE.bak-$(date +%Y%m%d%H%M%S)"
make install DEST="$DEST" >"$BUILD/make-install.log" 2>&1 || die "make install failed - see $BUILD/make-install.log"
# "make install" only copies Eggdrop's stock scripts when scripts/ does not exist yet. The bundle
# already has scripts/ (custom scripts), so copy the stock ones here without touching existing files.
SRCS="$BUILD/eggdrop-${VERSION}/scripts"
for f in "$SRCS"/*.tcl; do cp -n "$f" "$DEST/scripts/"; done
for f in autobotchk botchk weed; do [ -f "$DEST/scripts/$f" ] || install -m 755 "$SRCS/$f" "$DEST/scripts/$f"; done
mkdir -p "$DEST/help"
for f in "$SRCS"/help/*.help; do cp -n "$f" "$DEST/help/"; done
[ -f "$DEST/scripts/userinfo.tcl" ] || die "stock scripts were not installed into $DEST/scripts"
ok "Eggdrop stock scripts installed (userinfo.tcl, alltools.tcl, compat.tcl, autobotchk...)"
"$DEST/eggdrop" -v | head -1
ok "Eggdrop installed in $DEST (an existing eggdrop.conf / userfile / chanfile is never overwritten)"

# --- 4. sanity checks of the configuration ---------------------------------------------
say "Checking the configuration"
for var in vhost6 listen-addr vhost4; do
    ip="$(sed -n "s/^set $var \"\([^\"]*\)\".*/\1/p" "$DEST/eggdrop.conf" | head -1)"
    [ -n "$ip" ] || continue
    if ip -o addr 2>/dev/null | grep -qw -- "$ip"; then
        ok "$var $ip exists on this server"
    else
        warn "$var \"$ip\" is NOT an address of this server: edit it in $DEST/eggdrop.conf (or comment it out)"
    fi
done
chmod 600 "$DEST/eggdrop.conf" "$DEST/$USERFILE" "$DEST/$CHANFILE" 2>/dev/null || true
SVC="$DEST/scripts/service/service.conf"
[ -f "$SVC" ] && chmod 600 "$SVC"

# --- Q account (auto login with CHALLENGEAUTH, done by service.tcl) -------------------------
if [ -f "$SVC" ] && [ -t 0 ]; then
    CUR_USER="$(python3 - "$SVC" <<'PY'
import re, sys
s = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'array set qauth \{(.*?)\n\}', s, re.S)
u = re.search(r'"user"\s+"([^"]*)"', m.group(1)) if m else None
print(u.group(1) if u else "")
PY
)"
    say "Q account for the automatic login (Enter keeps the current value)"
    read -r -p "    Q account [${CUR_USER:-none}]: " Q_USER
    read -r -s -p "    Q password [keep current]: " Q_PASS; echo
    if [ -n "$Q_USER$Q_PASS" ]; then
        Q_USER="$Q_USER" Q_PASS="$Q_PASS" python3 - "$SVC" <<'PY'
import os, re, sys
path = sys.argv[1]
s = open(path, encoding="utf-8").read()
m = re.search(r'array set qauth \{(.*?)\n\}', s, re.S)
if not m:
    sys.exit("qauth section not found in service.conf")
block = m.group(1)
def tcl(v):  # value safe inside Tcl double quotes
    return "".join("\\" + c if c in '\\"$[]' else c for c in v)
for key, env in (("user", "Q_USER"), ("pass", "Q_PASS")):
    val = os.environ.get(env, "")
    if val:
        block = re.sub(r'("%s"\s+)"[^"]*"' % key, lambda mm: mm.group(1) + '"' + tcl(val) + '"', block, count=1)
s = s[:m.start(1)] + block + s[m.end(1):]
open(path, "w", encoding="utf-8").write(s)
PY
        chmod 600 "$SVC"
        ok "Q login saved in scripts/service/service.conf (CHALLENGEAUTH: the password is never sent)"
    fi
fi
if grep -q '"pass" ""' "$SVC" 2>/dev/null; then
    warn "no Q password in scripts/service/service.conf (qauth): the bot will not log in to Q"
fi

# --- 5. COD2BOT backend -----------------------------------------------------------------
if [ -x "$DEST/cod2bot/install.sh" ] || [ -f "$DEST/cod2bot/install.sh" ]; then
    say "Installing the COD2BOT backend"
    chmod +x "$DEST/cod2bot/install.sh" "$DEST/cod2bot/cod2botctl"
    "$DEST/cod2bot/install.sh" --eggdrop "$DEST" --no-check
fi

# --- 6. start + supervision -----------------------------------------------------------------
FRESH=0
[ -s "$DEST/$USERFILE" ] || FRESH=1
if [ "$START" = 1 ] && [ "$FRESH" = 1 ]; then
    cd "$DEST"
    say "No userfile: starting Eggdrop in userfile creation mode (-m)"
    ./eggdrop -m eggdrop.conf || warn "Eggdrop did not start - read the messages above and logs/eggdrop.log"
    OWNER="$(sed -n 's/^set owner "\([^"]*\)".*/\1/p' eggdrop.conf | head -1)"
    NICK="$(sed -n 's/^set nick "\([^"]*\)".*/\1/p' eggdrop.conf | head -1)"
    cat <<EOF

$(printf '\033[1;33m')FIRST START - create the owner now:$(printf '\033[0m')
  1. Wait until ${NICK} is on IRC (it waits for the Q login before joining channels).
  2. From IRC, with the nick ${OWNER:-<your nick>} (authed to Q with +x):   /msg ${NICK} hello
  3. Set your password:                                         /msg ${NICK} pass <password>
  4. Then enable the watchdog (restarts the bot if it dies / after reboot):
       cd ${DEST} && ./scripts/autobotchk eggdrop.conf -10 -noemail
EOF
elif [ "$START" = 1 ]; then
    cd "$DEST"
    if [ "$SUPERVISOR" = "systemd" ] && systemctl --user status >/dev/null 2>&1; then
        say "Creating the systemd user service for Eggdrop (autobotchk)"
        UNIT="$(sed -n 's/^set nick "\([^"]*\)".*/\1/p' eggdrop.conf | head -1 | tr -cd 'A-Za-z0-9_-')"
        if [ -f "$HOME/.config/systemd/user/${UNIT}.service" ]; then
            ok "unit ${UNIT}.service already exists"
            systemctl --user restart "${UNIT}.service"
        else
            ./scripts/autobotchk eggdrop.conf -systemd -unitname "$UNIT" || warn "autobotchk failed: start the bot with ./eggdrop eggdrop.conf"
        fi
        ok "Eggdrop supervised by systemd: systemctl --user status ${UNIT}"
    else
        say "Starting Eggdrop and adding the botchk crontab (every 10 min)"
        ./eggdrop eggdrop.conf || warn "Eggdrop did not start - read the messages above and logs/eggdrop.log"
        ./scripts/autobotchk eggdrop.conf -10 -noemail || warn "autobotchk (cron) failed"
    fi
fi

cat <<EOF

$(printf '\033[1;32m')Eggdrop ${VERSION} + COD2BOT installed in ${DEST}$(printf '\033[0m')

  Logs:        tail -f ${DEST}/logs/eggdrop.log
  Partyline:   telnet <server ip> <listen port>   (log in with your handle)
  Then:        .status   .cod2   .channels
  Q login:     scripts/service/service.conf (qauth) - automatic on every connect
EOF
