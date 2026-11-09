# Data sources

All checked on **2026-11-06** from this environment, with the honest User-Agent
`COD2BOT/1.0 (+IRC community bot)` and respecting `robots.txt`.

## Verified and active

| Id | Type | URL | Content | robots.txt / terms |
|---|---|---|---|---|
| `fpschallenge` | JSON API | `https://fpschallenge.eu/api/v1/news` | FPSChallenge news (only the *Call of Duty 2* category is kept): tournaments, results, LANs | `User-agent: * Allow: /`, `Content-Signal: search=yes, use=reference`: posting headline + link fits |
| — | JSON API | `https://fpschallenge.eu/api/v1/tournament`, `/tournament/{slug}`, `/tournament/{slug}/teams`, `/teams/{id}` | Tournaments, registration, check-in, map pool, teams, ladder (rank/ELO/W-L) | same |
| `zpam` | GitHub REST API | `https://api.github.com/repos/eyza-cod2/zpam3/releases` | zPAM releases (competitive mod) | Official API, 60 req/h without a token; the bot uses ETag |
| `cod2x` | GitHub REST API | `https://api.github.com/repos/callofduty2x/CoD2x/releases` | CoD2x patch releases | same |
| `moddb` | RSS | `https://rss.moddb.com/games/call-of-duty-2/articles/feed/rss.xml` | CoD2 mod articles | `Disallow: /` + `Allow: /*.xml` → feeds allowed |
| `moddb-files` | RSS | `.../call-of-duty-2/downloads/feed/rss.xml` | Mod downloads (not announced by default) | same |
| `moddb-addons` | RSS | `.../call-of-duty-2/addons/feed/rss.xml` | Maps and addons (not announced by default) | same |
| servers | UDP | `cod2master.activision.com:20710` + `getstatus` to each server | ~480 servers listed, ~400 answer, ~1700 players (2026-11-06) | the game's public protocol |

## Checked and rejected

| Source | Reason |
|---|---|
| FPSChallenge HTML site (`/news`) | Behind Cloudflare's bot challenge (403 "Just a moment..."). It is not circumvented: the public JSON API is used instead. With the site's permission, the right way is for its admin to allow the VPS IPs in Cloudflare |
| GitHub `releases.atom` feeds | `github.com/robots.txt` has `Disallow: /*.atom$` for every bot → the REST API is used |
| Reddit r/CallofDuty2 (`.rss`) | `reddit.com/robots.txt`: `User-agent: * Disallow: /`. Kept in the config, **disabled** |
| FPSChallenge RSS/feeds (`/feed`, `/rss`, `/sitemap.xml`...) | 403 Cloudflare; no public feed exists |
| FPSChallenge full match/standings endpoints | No public endpoint (`/api/v1/ladder`, `/matches`, `/league` → 404) |

> Technical note: Python's `urllib.robotparser` supports neither wildcards nor the RFC 9309
> "longest match wins" rule (it wrongly blocked ModDB and wrongly allowed GitHub's `*.atom`). COD2BOT
> ships its own RFC-compliant matcher (`app/core/http.py`, class `Robots`, with tests).

## Adding a new source

### From IRC (RSS/Atom)
```
!news addsource https://example.org/feed.xml example
```
The bot downloads the URL, checks it is RSS/Atom with entries, imports the existing items **without announcing
them** and polls it every `default_interval`. `!news removesource example` deletes it.

### In `config/news_sources.ini` (any type)
```ini
[mysite]
type = feed            ; fpschallenge_api | github_releases | rss | atom | feed | html
label = My Site        ; name shown in IRC
url = https://mysite.org/rss
interval = 1800        ; seconds (>= 600 recommended)
announce = yes         ; post automatically
category = community   ; category when the classifier finds none
colour = 12            ; mIRC colour of the source name
```
Releases of a GitHub repository:
```ini
[mymod]
type = github_releases
label = MyMod
url = https://api.github.com/repos/user/repo/releases?per_page=10
```
A site without a feed (HTML scraping with a regular expression; groups `url` and `title` are required,
`date`, `image`, `desc` optional). Check its `robots.txt` and terms of use first:
```ini
[clansite]
type = html
url = https://clan.example.org/news
base_url = https://clan.example.org/
item_pattern = <article>.*?<a href="(?P<url>[^"]+)">(?P<title>[^<]+)</a>.*?<time datetime="(?P<date>[^"]+)"
```
Then `!reload` (admin). If `robots.txt` forbids it, the source stays in error
"disallowed by robots.txt" and is not downloaded.

## Categories

Assigned by keywords (table `news_categories`, editable in the database): `results`, `tournaments`,
`lan`, `ladders`, `interviews`, `tools`, `mods`, `maps`, `servers`, `community`; if none matches, the
source's `category`.
