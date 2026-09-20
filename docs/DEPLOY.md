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

Code or corpus change → `fly deploy`. Corpus refresh without a code change is
the same command; the daily refresh job on the backlog will do it in place.

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
