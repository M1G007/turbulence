# Market Turbulence Model

Live dashboard: https://m1g007.github.io/turbulence/

The daily refresh runs free on GitHub Actions (`.github/workflows/daily-refresh.yml`), Mon-Fri at 2:15 PM Arizona time (after the close year-round); each run includes that day's close.

- `src/` holds the model code (`turbulence-app/server.py`, `build-static.py`). The workflow does nothing until it exists.
- Repo variable `LIVE`: `true` = update the live site and send alerts; anything else = shadow mode (build and compare only).
- Repo variable `ALERT_MODE`: `elevated` (email only when the signal is not CALM) or `daily`.
- Alerts arrive as GitHub issues labelled `turbulence-alert`, which GitHub emails to the repo owner.
