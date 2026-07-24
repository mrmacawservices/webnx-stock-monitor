# webnx-stock-monitor

Watches a WebNX store category page (default: NYC Instant Ryzen,
`https://clients.webnx.com/index.php?rp=/store/nyc-instant-ryzen`) and
posts a Discord/Slack webhook message whenever:

- a new machine is listed
- a listing's stock quantity changes
- a listing's price changes
- a listing disappears (sold out / delisted)

## Setup

1. Edit [`config.json`](config.json) and replace `webhook_url` with your
   Discord or Slack incoming webhook URL. Until you do, changes are just
   logged to `monitor.log` and printed to the console instead of posted.
2. (Optional) Change `url`/`label` to point at a different store category,
   or `interval_minutes` to change how often `--loop` checks.

Dependencies are already installed in the bundled `.venv`.

## Running

One-off check:

```
.venv\Scripts\python.exe monitor.py
```

Run forever, checking every `interval_minutes` (default 5):

```
.venv\Scripts\python.exe monitor.py --loop
```

The first run just records a baseline snapshot (`snapshot.json`) — you'll
only get notified starting from the second check onward, once there's
something to compare against.

## Running on a schedule

`--loop` only runs while the window is open, and needs your PC on. Two free
always-on options:

### Option A — GitHub Actions (full detail, no PC required)

This repo includes [`.github/workflows/monitor.yml`](.github/workflows/monitor.yml),
which runs `monitor.py` on a 5-minute cron in GitHub's cloud and commits the
updated `snapshot.json` back to the repo so state persists between runs.

Setup:

1. The repo must be **public** for the schedule to run on unlimited free
   Actions minutes (private repos only get 2,000 free minutes/month, which a
   5-minute cron burns through in a few days). No secrets live in the code —
   the webhook URL is injected via a GitHub Actions secret, never committed.
2. Add the webhook URL as a repo secret:
   ```
   gh secret set WEBHOOK_URL --repo <owner>/webnx-stock-monitor
   ```
3. That's it — the workflow runs automatically on its schedule. Trigger a
   manual run anytime from the Actions tab, or:
   ```
   gh workflow run monitor.yml --repo <owner>/webnx-stock-monitor
   ```

Note: GitHub's cron scheduler is best-effort — under load, runs can be
delayed several minutes past the 5-minute mark.

### Option B — Windows Task Scheduler (local, runs full script)

```
schtasks /create /tn "WebNX Stock Monitor" /tr "\"C:\Users\harri\Documents\Projects\webnx-stock-monitor\.venv\Scripts\python.exe\" \"C:\Users\harri\Documents\Projects\webnx-stock-monitor\monitor.py\"" /sc minute /mo 5 /f
```

Remove it later with:

```
schtasks /delete /tn "WebNX Stock Monitor" /f
```

### Option C — UptimeRobot keyword monitor (zero code, binary alert only)

UptimeRobot's free plan can watch the page directly with no script at all,
but it can only tell you "at least one machine is now listed" — not which
one, its stock count, or its price. Good as a redundant backstop:

1. Sign in to UptimeRobot and add a new **Keyword** monitor.
2. URL: `https://clients.webnx.com/index.php?rp=/store/nyc-instant-ryzen`
3. Keyword: `Product group does not contain any visible products`
4. Alert when: **keyword NOT exists** (fires the moment that phrase
   disappears, i.e. a machine becomes listed).
5. Interval: 5 minutes (free plan minimum).
6. Under Alert Contacts, add a Slack/Discord webhook integration (or email)
   to receive the alert.

## Files

- `monitor.py` — fetch/parse/diff/notify logic
- `config.json` — target URL, webhook URL, check interval
- `snapshot.json` — last-seen product state (auto-created, gitignored)
- `monitor.log` — running log of checks and detected changes (gitignored)
