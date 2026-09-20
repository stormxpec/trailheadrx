# Deploying Trailhead Rx to Fly.io

Session 5. One small machine in Chicago, a 1 GB disk for what the app writes,
secrets held by Fly, HTTPS on trailheadrx.com. Plain-English notes on each
step follow the commands. Run everything from the repo root on the MacBook
Air, with the venv active.

## What lives where

| Thing | Where | Why |
|---|---|---|
| Code, templates, rules, manifests | the image (built from your working copy) | rebuilt on every `fly deploy` |
| Corpus documents (`corpus/policies/*`) | the image | payer-published files; kept out of the public repo, fine in a private image |
| Search index + embedding model | the image, built during `fly deploy` | a machine starts ready; nothing fetched at request time |
| API key, access code, cookie secret | Fly secrets | never in the repo, never in the image |
| Audit trace, plan-name requests | the `/data` volume | survive deploys and restarts |

## One-time setup

```bash
fly auth whoami                       # prints your email — already done
fly apps create trailheadrx           # claims the name; trailheadrx.fly.dev
fly volumes create trailheadrx_data --region ord --size 1   # 1 GB disk for /data
```

Secrets. The access code is the one from your local `.env`; the cookie secret
is new and random — it signs the sign-in cookie, so it must not change casually
(changing it signs everyone out, which is also how you would kick everyone out
if a code leaked).

```bash
fly secrets set \
  ANTHROPIC_API_KEY="sk-ant-..." \
  TRAILHEADRX_ACCESS_CODE="the code from your .env" \
  TRAILHEADRX_SECRET="$(openssl rand -hex 32)"
```

Type the key in from the Anthropic console rather than pasting from a chat or
screenshot; it never needs to appear anywhere but that command.

## Deploy

```bash
fly deploy
```

The first build takes several minutes: it installs PyTorch (CPU build), copies
the corpus, builds the index, and downloads the embedding model into the
image. Later deploys reuse the layers that did not change. When it finishes:

```bash
fly status                             # one machine, "started"
fly logs                               # watch the warm-up; Ctrl-C to stop
open https://trailheadrx.fly.dev       # the access-code page
```

Try one question end to end before touching DNS.

## Domain: trailheadrx.com (GoDaddy)

Tell Fly about the names, and it will issue certificates:

```bash
fly certs add trailheadrx.com
fly certs add www.trailheadrx.com
fly ips list                           # note the IPv4 and IPv6 addresses
```

In GoDaddy → My Products → trailheadrx.com → DNS, add:

| Type | Name | Value | TTL |
|---|---|---|---|
| A | @ | the IPv4 from `fly ips list` | 600 |
| AAAA | @ | the IPv6 from `fly ips list` | 600 |
| CNAME | www | trailheadrx.fly.dev | 600 |

Delete any GoDaddy "parked" A record or forwarding rule on `@` first, or the
two will fight. `fly certs add` may also print an `_acme-challenge` CNAME; add
it if asked. Then:

```bash
fly certs check trailheadrx.com        # "Ready" once DNS has spread (minutes to an hour)
```

`force_https = true` in fly.toml sends http:// visitors to https://, and the
app sets its cookie `Secure` and a strict transport header when
`TRAILHEADRX_HTTPS=1` (set in the Dockerfile).

## Cost

The machine is `shared-cpu-1x` with 2 GB of memory (the embedding model needs
about 1.2 GB). With `auto_stop_machines = "suspend"` it pauses when idle and
resumes in a second or two with the model still in memory, so the running cost
is a few dollars a month plus the disk; always-on would be roughly $10–12 a
month. The Anthropic spend is separate and still capped by the app at $5 per
UTC day (`daily_spend_cap_usd` in config.yaml); raise it there when the friend
at Lilly starts testing.

## Updating

Code change → `fly deploy`. Corpus changes arrive on their own through the
daily refresh (below); a manual `fly deploy` also re-indexes whatever is in
your local corpus folder.

Secrets change → `fly secrets set NAME=value` (restarts the machine).

Bigger spend cap or rate limit → edit config.yaml, `fly deploy`.

## Checking on it

```bash
fly status                             # machine state
fly logs                               # live log; answers, refusals, spend
fly ssh console                        # a shell on the machine
  ls /data/audit                       #   the audit trace lives here
  python -m trailheadrx trace          #   the CLI works on the machine too
```

Nothing personal is written anywhere: refused questions are never logged, free
text is never cached, and plan-name requests record the name and the date
only. The audit trace on `/data` holds question text for answered questions,
the same as on the laptop.

## If something goes wrong

- Build fails on memory: `fly deploy --remote-only` uses Fly's builder, which
  has more headroom than the laptop's Docker.
- Machine restarts in a loop: `fly logs` will show a Python error; the health
  check waits 60 s for the model to load before it starts counting.
- "That code did not match" on a code you know is right: `fly secrets list`
  to confirm `TRAILHEADRX_ACCESS_CODE` is set; a trailing space in the
  `fly secrets set` line is the usual cause.
- Roll back: `fly releases` lists deploys; `fly deploy --image <previous image>`
  puts one back.

## Daily corpus refresh (session 5, part 2)

`python -m trailheadrx refresh` re-fetches every policy document and every
maker program page, fingerprints the content (visible text for HTML, bytes
for PDF), saves and re-indexes any policy that changed, records a checked-on
date for everything, and emails a report when something changed or a fetch
newly failed. A quiet night sends nothing. Program pages are never edited
automatically: the report tells you which one to re-read, because the terms
in `corpus/programs/manifest.yaml` are hand-verified.

State: `refresh_state.json` (on the server: `/data/refresh_state.json`), keyed
by URL — last hash, last checked, last changed, last error. The footer of
every page shows "Plan documents last checked <date>" from it.

Email uses Resend (resend.com — free tier is plenty). Create an account, make
an API key, then:

```bash
fly secrets set RESEND_API_KEY="re_..." REFRESH_EMAIL_TO="stormerb@gmail.com"
```

Until a sending domain is verified, Resend delivers from `onboarding@resend.dev`
to the account's own address only, which is exactly this use. To send from
`@trailheadrx.com` later, verify the domain in Resend (three DNS records at
GoDaddy) and set `REFRESH_EMAIL_FROM="Trailhead Rx <refresh@trailheadrx.com>"`.

Schedule: `.github/workflows/refresh.yml` runs daily at 5:17 am Eastern and
runs the commands inside the live machine with `fly machine exec` (Fly's API, no SSH tunnel), so it uses the
same index and disk the site does. One-time setup:

```bash
fly tokens create deploy -x 999999h        # prints a token; copy it
```

GitHub → repo → Settings → Secrets and variables → Actions → New repository
secret → name `FLY_API_TOKEN`, value the token. The Actions tab then has a
"corpus refresh" workflow with a "Run workflow" button for a manual check.

Run it by hand on the server any time: `fly ssh console -C "python -m trailheadrx refresh"`.
On the laptop: `make refresh` (prints the report; emails only if the two
Resend variables are in your `.env`).

## Nightly sweep (session 5, part 3)

`python -m trailheadrx sweep` answers the menu ahead of time — every held plan
× medicine × preset question — through the same `compute()` the website uses,
so those requests are served instantly, and compares each answer's summary,
numbered steps and lead-time months with the previous sweep's to flag drift.
The answers are written to `/data/cache` on the server (`TRAILHEADRX_CACHE_DIR`),
so a restart keeps them; the sweep's own record is `/data/sweep_record.json`.

Cost control: `precompute.daily_budget_usd` in config.yaml (default $3) stops
the sweep for the night; it resumes where it left off the next night. The
GitHub workflow runs it with `--pairs 12` — the twelve most-asked plan ×
medicine pairs from the audit trace — so early on it costs cents. Widen it in
`.github/workflows/refresh.yml` when the corpus and the audience grow. The
same Resend secrets carry the drift report; a quiet night sends nothing.

`make sweep` on the laptop is a dry run (counts what would run, no model calls).
