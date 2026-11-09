#!/usr/bin/env bash
# COD2BOT installer for a Linux VPS (Debian/Ubuntu tested layout).
# Safe to run again: it never overwrites your config, .env or database.
#
#   ./install.sh [--eggdrop /path/to/eggdrop] [--no-systemd] [--no-check]
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
EGG=""
SYSTEMD=1
CHECK=1
while [ $# -gt 0 ]; do
    case "$1" in
        --eggdrop) EGG="$2"; shift 2 ;;
        --no-systemd) SYSTEMD=0; shift ;;
        --no-check) CHECK=0; shift ;;
        -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
        *) echo "unknown option $1"; exit 1 ;;
    esac
done

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERR\033[0m %s\n' "$*"; exit 1; }

# --- 1. locate Eggdrop -----------------------------------------------------
if [ -z "$EGG" ]; then
    if [ -f "$ROOT/../eggdrop.conf" ]; then EGG="$(cd "$ROOT/.." && pwd)"
    elif [ -f "$HOME/eggdrop/eggdrop.conf" ]; then EGG="$HOME/eggdrop"
    fi
fi
[ -n "$EGG" ] && [ -f "$EGG/eggdrop.conf" ] || die "Eggdrop not found: use --eggdrop /path/to/eggdrop (folder with eggdrop.conf)"
CONF="$EGG/eggdrop.conf"
ok "Eggdrop: $EGG"

# --- 2. Python ----------------------------------------------------------------
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 not found (apt install python3)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "Python >= 3.9 needed ($("$PY" -V))"
ok "$("$PY" -V)"
"$PY" -c 'import zoneinfo; zoneinfo.ZoneInfo("Europe/Berlin")' 2>/dev/null && ok "tzdata" \
    || say "WARNING: timezone data missing (apt install tzdata) - times will be shown in UTC"
command -v tclsh >/dev/null 2>&1 && ok "tclsh found (Tcl integration test enabled)" || say "tclsh not found: Tcl test skipped (optional)"

# --- 3. folders, secrets ------------------------------------------------------
say "Preparing folders and secrets"
mkdir -p "$ROOT/data" "$ROOT/logs" "$ROOT/backups"
chmod 700 "$ROOT/data" "$ROOT/backups"
[ -f "$ROOT/.env" ] || { cp "$ROOT/.env.example" "$ROOT/.env"; ok "created .env"; }
chmod 600 "$ROOT/.env"
chmod +x "$ROOT/cod2botctl"
TOKEN_FILE="$ROOT/data/api.token"
(cd "$ROOT/backend" && COD2BOT_ROOT="$ROOT" "$PY" -c 'from app.core.config import Config; Config().api_token()')
[ -f "$TOKEN_FILE" ] && chmod 600 "$TOKEN_FILE"
ok "API token ready (data/api.token)"

# --- 4. Eggdrop script ----------------------------------------------------------
say "Installing the Eggdrop script"
DEST="$EGG/scripts/cod2bot"
mkdir -p "$DEST"
cp "$ROOT/eggdrop/scripts/cod2bot/cod2bot.tcl" "$DEST/cod2bot.tcl"
if [ ! -f "$DEST/cod2bot.conf" ]; then
    cp "$ROOT/eggdrop/scripts/cod2bot/cod2bot.conf" "$DEST/cod2bot.conf"
    ok "created $DEST/cod2bot.conf"
fi
# absolute token path (Eggdrop's working directory may change)
sed -i "s#^\(\s*variable tokenfile \).*#\1\"$TOKEN_FILE\"#" "$DEST/cod2bot.conf"
API_PORT="$(sed -n 's/^port *= *\([0-9]*\).*/\1/p' "$ROOT/config/cod2bot.ini" | head -1)"
sed -i "s#^\(\s*variable api \).*#\1\"http://127.0.0.1:${API_PORT:-8765}\"#" "$DEST/cod2bot.conf"
if ! grep -q 'scripts/cod2bot/cod2bot.tcl' "$CONF"; then
    cp "$CONF" "$CONF.bak-$(date +%Y%m%d%H%M%S)"
    printf '\n# COD2BOT - Call of Duty 2 community bot\nsource scripts/cod2bot/cod2bot.tcl\n' >>"$CONF"
    ok "added 'source scripts/cod2bot/cod2bot.tcl' to eggdrop.conf (backup kept)"
else
    ok "eggdrop.conf already loads cod2bot.tcl"
fi

# --- 5. tests ---------------------------------------------------------------------
say "Running tests"
(cd "$ROOT/backend" && "$PY" -m unittest discover -s tests 2>&1 | tail -3)

# --- 6. service ---------------------------------------------------------------------
if [ "$SYSTEMD" = 1 ] && command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
    say "Installing systemd user service"
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    sed -e "s#@ROOT@#$ROOT#g" -e "s#@PYTHON@#$PY#g" "$ROOT/deploy/systemd/cod2bot-backend.service" \
        >"$UNIT_DIR/cod2bot-backend.service"
    systemctl --user daemon-reload
    systemctl --user enable --now cod2bot-backend.service
    ok "cod2bot-backend running: systemctl --user status cod2bot-backend"
    if command -v loginctl >/dev/null 2>&1 && ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
        say "Run once (as root or with sudo) so it keeps running after logout and reboots:"
        echo "      sudo loginctl enable-linger $USER"
    fi
elif command -v crontab >/dev/null 2>&1; then
    say "systemd user services not available: using crontab (start at boot + watchdog every 5 min)"
    CRON_LINE="*/5 * * * * $ROOT/cod2botctl start >/dev/null 2>&1"
    ( crontab -l 2>/dev/null | grep -v 'cod2botctl start' || true
      echo "@reboot $ROOT/cod2botctl start >/dev/null 2>&1"; echo "$CRON_LINE" ) | crontab -
    "$ROOT/cod2botctl" start
else
    say "Neither systemd nor crontab available: start the backend with $ROOT/cod2botctl start"
    "$ROOT/cod2botctl" start
fi

# --- 7. live check ---------------------------------------------------------------------
if [ "$CHECK" = 1 ]; then
    say "Checking news sources, FPSChallenge API and CoD2 master server (takes ~2 minutes)"
    (cd "$ROOT/backend" && "$PY" -m app --check) || say "some sources failed (see above) - the bot retries automatically"
fi

cat <<EOF

$(printf '\033[1;32m')COD2BOT installed.$(printf '\033[0m')

  1. On the Eggdrop partyline:   .rehash
  2. Enable channels:             .chanset #yourchannel +cod2   (or enable_chans in cod2bot.conf)
  3. Check:                       .cod2      (partyline)   ·   !cod2   (in the channel)

  Backend:  $ROOT/cod2botctl status|restart|logs|check|backup|update
  Config:   $ROOT/config/cod2bot.ini  ·  $ROOT/config/news_sources.ini  ·  $DEST/cod2bot.conf
EOF
