# Reggie App Runner

Run multiple git-sourced applications behind one Caddy frontend in a Databricks App. Configuration is driven by environment variables using Dynaconf.

## Run command

`run` is optional now. All of these are valid:

- `pixi run uvr reggie-app-runner`
- `pixi run uvr reggie-app-runner run`
- `pixi run uvr python -m reggie_app_runner`

## Configuration model

Per-app settings use indexed prefixes:

- `DATABRICKS_APP_RUNNER_<N>_SOURCE` (required)
- `DATABRICKS_APP_RUNNER_<N>_PATH` (optional, defaults from repo name)
- `DATABRICKS_APP_RUNNER_<N>_STRIP_PATH_PREFIX` (optional, default true)
- `DATABRICKS_APP_RUNNER_<N>_ENV` (optional JSON or YAML map)
- `DATABRICKS_APP_RUNNER_<N>_COMMAND` (optional, falls back to discovered `app.yaml`)

## Deployment option 1: deploy this repo as an app bundle

Clone this repo, customize environment variables in your own Databricks bundle/app resource, then deploy.

Example `databricks.yml` snippet:

```yaml
bundle:
  name: reggie-app-runner

resources:
  apps:
    reggie_app_runner:
      name: reggie-app-runner
      source_code_path: .
      description: Multi-app runner
```

Example app command and env in your app resource or `app.yaml`:

```yaml
command:
  - bash
  - -lc
  - pixi run uvr reggie-app-runner

env:
  - name: DATABRICKS_APP_RUNNER_0_SOURCE
    value: git+https://github.com/jjaiwant328/racetrac-store-intelligence.git@reggie-chat
  - name: DATABRICKS_APP_RUNNER_0_PATH
    value: /racetrac-store-intelligence
```

## Deployment option 2: use package in requirements.txt and run from app.yaml

If you do not want to deploy this repo directly, install it as a dependency and start it from your Databricks App `app.yaml`.

Example `requirements.txt` entry:

```txt
reggie-app-runner @ git+https://github.com/<your-org>/reggie-app-runner.git
```

Example `app.yaml`:

```yaml
command:
  - bash
  - -lc
  - python -m reggie_app_runner

env:
  - name: DATABRICKS_APP_RUNNER_0_SOURCE
    value: git+https://github.com/jjaiwant328/racetrac-store-intelligence.git@reggie-chat
  - name: DATABRICKS_APP_RUNNER_1_SOURCE
    value: git@github.com:reggie-db/store-intelligence.git
```

## Notes

- Root Caddy listens on `DATABRICKS_APP_PORT` (falls back to `8000` locally).
- Summary UI is served at `/` unless one app owns `/`, then it is served at `/_app_runner`.
- Unmatched routes are redirected/routed to the summary UI.
