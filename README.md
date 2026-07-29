# RPS Medical Fleet Command

Interactive SkyTrackIt dashboard for RPS Medical fleet operations.

## Contents

- `index.html` - Interactive dashboard with embedded fleet data.
- RPS Medical logo and healthcare imagery are loaded from `www.rpsmedical.com`.

## Prepared Dashboard Data

- 10 vehicles
- 256 routes
- 1,332 stops
- 38 speeding events over 65 mph

## Dashboard Features

- Live-style operational map with route, stop, speed, and heatmap layers.
- Alert panel for stopped vehicles, stale reports, and speed events.
- Route review panel explaining why each route is inefficient or needs review, plus the operational alternative to make it efficient.
- Stop hotspot panel for frequent stop zones.
- CSV export for the active table.
- Route playback for a critical route.
- Quick filters for moving, stopped, and risk vehicles.
- Clickable KPI cards with hover explanations and links to the relevant section.
- Sortable tables with row counts.
- Operational risk score per vehicle.
- Operational risk explanation panel with causes and actions to lower the score.
- Stale-report KPI based on report generation time.
- Control-room strip with latest signal, refresh cadence, worst route ratio, top stop unit, and top speeding unit.
- Auto-generated action plan for stopped vehicles, route inefficiency, speeding, and GPS signal gaps, with direct steps to reduce risk.
- Route quality summary separating efficient, review, and inefficient trips. Route tables include cause, alternative action, and estimated miles that could be saved.
- Browser password gate. Current access password: `melvinmelvin`.
- Local auto-refresh scheduled every 5 minutes from the publishing machine.

Open `index.html` directly or serve this repository with GitHub Pages.

## Live Refresh

`scripts/live_refresh.ps1` refreshes the dashboard every 5 minutes from the Windows machine where this repo is published.

Required user environment variables on that machine:

- `SKYTRACKIT_USER`
- `SKYTRACKIT_PASSWORD`

Each run downloads fresh SkyTrackIt data, rebuilds `index.html`, commits the updated dashboard, and pushes it to GitHub Pages.

