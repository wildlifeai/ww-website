# Public API guide

How a partner reads an organisation's Wildlife Watcher data with an API key: getting a key,
authentication, scopes, paging, errors, rate limits and the CamtrapDP export. Every endpoint and
its parameters are in the [API reference](api-reference.md#public-data-api-v1), and the running
API describes itself at `/docs`.

## Get a key

An organisation manager creates the key in Settings, Integrations: a name, the scopes it needs
and, optionally, an expiry date. The key (`ww_live_` and 32 hex characters) is shown once, so copy
it then. The same page revokes it. A key belongs to one organisation and reads only that
organisation's projects.

## Authenticate

Send the key in the `X-API-Key` header. The base URLs are at the top of the
[API reference](api-reference.md).

```bash
export WW_API=https://ww-backend.bravesand-8bd2f1d4.australiaeast.azurecontainerapps.io
export WW_KEY=ww_live_...

curl -s -H "X-API-Key: $WW_KEY" "$WW_API/api/v1/deployments"
```

## Scopes

| Scope | Lets the key call |
|---|---|
| `deployments:read` | `GET /api/v1/deployments`, `GET /api/v1/deployments/{deployment_id}` |
| `devices:read` | `GET /api/v1/devices` |
| `telemetry:read` | `GET /api/v1/devices/{device_eui}/telemetry` |
| `observations:read` | `GET /api/v1/observations` |
| `export:camtrapdp` | `POST /api/v1/export/camtrapdp`, `GET /api/v1/jobs/{job_id}` |
| `models:read` | Nothing yet |

## What a key sees

- **Deployments** in the organisation's projects. Deleted deployments, and those of a deleted
  project, are left out.
- **Devices**: the organisation's own cameras.
- **Telemetry**: the LoRaWAN messages a camera sent while deployed in one of the organisation's
  projects. A camera lent to another organisation's project sends that organisation's messages.
- **Observations**: one row per photo, with the verdict the website's photo grid shows. A
  person's verdict comes first, then the consensus of the AI models, then the first AI label.
  `is_empty` is true for a photo with no animal, person or vehicle. Only photos with at least one
  observation are listed, and deleted photos are left out.

## Responses and paging

Every answer is `{"data": ..., "error": null, "meta": {...}}`. The lists take `limit` and
`offset`; `meta.total` is the number of rows across all pages. A page holds at most 1,000 rows,
and a larger `limit` answers 422. Observations come in the order the photos were added to the
website, so new photos land on the last page. Deployments and devices come newest first.

```bash
offset=0
while :; do
  page=$(curl -s -H "X-API-Key: $WW_KEY" "$WW_API/api/v1/observations?limit=1000&offset=$offset")
  echo "$page" | jq -c '.data[]'
  [ "$(echo "$page" | jq '.data | length')" -lt 1000 ] && break
  offset=$((offset + 1000))
done
```

`project_id` and `deployment_id` narrow the observations, for example
`/api/v1/observations?deployment_id=<uuid>`.

## Errors

| Status | Meaning |
|---|---|
| 401 | No key, or the key is unknown, revoked or expired |
| 403 | The key lacks the endpoint's scope |
| 404 | Not found, including a deployment, project or job of another organisation. Also while the API, or the export, is switched off |
| 422 | A parameter is invalid, such as a `limit` above 1,000 or an id that is not a UUID |
| 429 | Rate limit reached. Wait the seconds in the `Retry-After` header |

The body of an error is `{"detail": "..."}`.

## Rate limits

A key may make 60 calls a minute across all the endpoints, and start 5 exports a minute. Past
either limit the API answers 429 with `Retry-After`. Each key has its own allowance, wherever its
calls come from.

## CamtrapDP export

The export is the package a project member gets from the website's Download button: a Camtrap DP
1.0 package with `datapackage.json`, `deployments.csv`, `media.csv`, `observations.csv` and every
original photo at `media/<deploymentID>/<mediaID>.<ext>`. It covers one project, narrowed by
deployments or by when the deployments started.

```bash
job=$(curl -s -X POST -H "X-API-Key: $WW_KEY" -H "Content-Type: application/json" \
  -d '{"project_id": "<uuid>", "date_from": "2026-01-01T00:00:00Z"}' \
  "$WW_API/api/v1/export/camtrapdp" | jq -r .data.job_id)

while :; do
  state=$(curl -s -H "X-API-Key: $WW_KEY" "$WW_API/api/v1/jobs/$job")
  case $(echo "$state" | jq -r .data.status) in completed|completed_with_errors|failed) break ;; esac
  sleep 30
done

curl -s -o export.zip "$(echo "$state" | jq -r .data.result_url)"
```

The job reports `status`, `progress` (0 to 1) and a `message`. Once it is `completed`,
`result_url` is a signed link that works for 24 hours. `completed_with_errors` means some
originals could not be read: `media.csv` still lists them and the package's description names
them. `failed` carries the reason in `error`. Only the organisation's keys can read its jobs.
