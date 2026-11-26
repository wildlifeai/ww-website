# Testing LoRaWAN on cloud dev with a real camera

> **Last updated:** 27 Nov 2026, first run end to end (WILD-W3CL, BLE 0.30.62, AI from Seeed `dev`).

A bench Wildlife Watcher, a gateway, The Things Network (TTN) and cloud dev, so the Realtime page
(`/field`) shows real messages. For the website, app and firmware teams. The ingest itself, its
tokens and the byte format are ww-backend's:
[LORAWAN_INGEST.md](https://github.com/wildlifeai/ww-backend/blob/dev/documentation/resources/LORAWAN_INGEST.md).

## What a dev reset takes with it

Every merge into ww-backend `dev` wipes cloud dev and reseeds it (`supabase db reset --linked`,
see [gotchas](../../.agents/skills/references/gotchas.md#cloud-dev-is-reseeded-without-notice)).

| Gone after each reset | Kept |
|---|---|
| Organisation ingest tokens (`lorawan_ingest_tokens`) | The `LORAWAN_INGEST_TOKEN` function secret |
| The bench camera's `devices` row and its deployment | The TTN webhook, the camera's TTN registration |
| Every LoRaWAN message | The camera's own settings (op 32) |

So the webhook uses the function secret, set once, and only the SQL below is redone after a reset.

## Once: point TTN at dev

Victor holds the TTN console. The cameras' application on the `au1` cluster
(`wildlife-watcher-test`) has three webhooks: Charles's trap.nz script, `ww-supabase-staging` and
`ww-dev-ingest`. Leave the first two alone.

1. **Set dev's secret to a value you hold.** Supabase never shows a secret's value, so set a new
   one (PowerShell):

   ```powershell
   $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); $t = ($b | ForEach-Object { $_.ToString('x2') }) -join ''; $t
   npx supabase secrets set "LORAWAN_INGEST_TOKEN=$t" --project-ref qegeovogqxiouqbrxmnh
   ```

   Keep it in a password manager. Never put it in an issue, a commit or a chat.
2. **The webhook.** TTN console, the application, **Integrations** → **Webhooks** →
   `ww-dev-ingest` (or **Add webhook** → **Custom webhook**): format JSON, Base URL
   `https://qegeovogqxiouqbrxmnh.supabase.co/functions/v1/lorawan-ingest?token=<the secret>`,
   **Uplink message** ticked with its path left empty. Nothing else.
3. **Check the token** with a synthetic message (stored against a test DevEUI, linked to nothing):

   ```powershell
   Invoke-RestMethod -Method Post -Uri "https://qegeovogqxiouqbrxmnh.supabase.co/functions/v1/lorawan-ingest?token=$t" -ContentType 'application/json' -Body '{"end_device_ids":{"dev_eui":"70B3D57ED0070001"},"uplink_message":{"f_port":2,"frm_payload":"U+gDUSoAE8hUXxIWVQIABwMwAAE="}}'
   ```

   `ok: True` with `decoded.kind` `report` is right; a 401 is the wrong token.

## After every reset: put the camera back on dev

In the dev SQL editor (project **Dev_Wildlife_Watcher**, never Stag), run this. It gives the
camera its DevEUI and an open deployment on a 5-minute ping, with a GPS position so it shows on
the Realtime map, in the seeded project *Wētāpunga Tracking Program*. For another camera, change
the name and DevEUI (TTN lists it under **End devices**). Safe to run twice.

These are test rows on cloud dev, which the next reset wipes. Staging is the live tier: no row is
written there by hand, and every change to it is a ww-backend migration.

```sql
DO $$
DECLARE
  _name constant text := 'WILD-W3CL';
  _eui constant text := '3B4AFAFEFFDA68D8';
  _project constant uuid := 'c0000000-0000-0000-0000-000000000001';
  -- Any position in New Zealand; the Realtime map leaves out a camera without one.
  _lat constant double precision := -41.2865;
  _lon constant double precision := 174.7762;
  _device uuid;
  _deployment uuid;
BEGIN
  SELECT id INTO _device FROM public.devices
   WHERE deleted_at IS NULL AND (name = _name OR device_eui ILIKE _eui)
   ORDER BY (device_eui ILIKE _eui) DESC NULLS LAST LIMIT 1;
  IF _device IS NULL THEN
    INSERT INTO public.devices (bluetooth_id, name, device_eui)
    VALUES ('bench-' || _name, _name, _eui) RETURNING id INTO _device;
  ELSE
    UPDATE public.devices SET device_eui = _eui WHERE id = _device;
  END IF;

  SELECT id INTO _deployment FROM public.deployments
   WHERE device_id = _device AND deployment_end IS NULL AND deleted_at IS NULL;
  IF _deployment IS NULL THEN
    INSERT INTO public.deployments (name, deployment_start, location_name, project_id, device_id,
                                    device_eui, lorawan_ping_minutes, timezone, latitude, longitude)
    VALUES ('Bench LoRaWAN test', now(), 'Bench', _project, _device, _eui, 5, 'Pacific/Auckland',
            _lat, _lon)
    RETURNING id INTO _deployment;
  ELSE
    UPDATE public.deployments
       SET lorawan_ping_minutes = 5, latitude = coalesce(latitude, _lat), longitude = coalesce(longitude, _lon)
     WHERE id = _deployment;
  END IF;
  RAISE NOTICE 'device %, deployment %', _device, _deployment;
END $$;
```

The camera's next message links to the deployment. Messages that arrived before the SQL ran stay
unlinked.

## The camera

- **Firmware:** the AI build from Seeed `dev` and Bluetooth 0.30.62 or later (both in the dev
  firmware catalogue). Older Bluetooth firmware sends the old message, with no health.
- **On TTN:** its DevEUI listed under **End devices** in the same application.
- **A gateway in range** on TTN `au1`, frequency plan *Asia 920-923 MHz (used by TTN Australia)*.
- **A short ping:** in the app's Engineer Console, `AI setop 32 5` (every 5 minutes); `AI setop 32 0`
  turns LoRaWAN off. The camera joins at once and sends its first message straight after.

## Check it worked

1. **TTN:** the device's **Live data** shows the join, then uplinks on FPort 2; the webhook shows
   Healthy.
2. **Dev database:**

   ```sql
   SELECT m.received_at, m.deployment_id IS NOT NULL AS linked, p.kind, p.battery_mv, p.sd_free_pct,
          p.temperature_c, p.self_test_bits, p.last_reset_reason, p.reset_count, p.op_index, p.op_value, p.op_error
   FROM public.lorawan_messages m
   LEFT JOIN public.lorawan_parsed_messages p ON p.lorawan_message_id = m.id
   WHERE m.device_eui ILIKE '3B4AFAFEFFDA68D8'
   ORDER BY m.received_at DESC LIMIT 20;
   ```

   and `deployment_latest_lorawan` for the deployment: `health_state`, `health_reasons`,
   `lorawan_ping_minutes`.
3. **Website:** https://dev.ww-website.pages.dev, signed in as a seed user of the project
   (`kowhai@ww.org` admin, `rata@ww.org` member, `tui@ww.org` `ww_admin`; the seed password comes
   from the team, see [testing-with-seed-users](testing-with-seed-users.md)), **Realtime**,
   project *Wētāpunga Tracking Program*: the camera under **Cameras**, its messages under
   **Messages**.

## Make the camera report something

| Do | Expect |
|---|---|
| Pull the SD card while the camera sleeps, then wake it | Self-test bit 11 at once: **Fault**, "SD card missing" |
| Put the card back and press RESET | Restart reason 7 (reset switch): no warning |
| Update its Bluetooth firmware | Restart reason 10, shown as an unexpected restart until [ww-backend#398](https://github.com/wildlifeai/ww-backend/issues/398) |
| TTN **Messaging** → **Downlink**, FPort `3`, payload `56 10 00 00` | After the camera's next uplink, its answer `56 10 <lo> <hi>`: "Setting 16 read: ..." under **Messages** |
| The same with `57 10 3C 00` | Sets op 16 (detection threshold) to 60 %: "Setting 16 set to 60" |

A downlink waits for the camera's next uplink (up to one ping period), and only one request goes
per downlink.

## Without a camera

Post synthetic messages to dev's `lorawan-ingest` (LORAWAN_INGEST.md, "Verifying an environment"),
or insert rows the way `e2e/specs/04-lorawan-realtime.spec.ts` does. The byte layouts and worked
examples are in ww-hardware's
[WW500_LoRaWAN_payloads.md](https://github.com/wildlifeai/ww-hardware/blob/dev/_Documentation/development%20reports/2026-11-06_lorawanPing/WW500_LoRaWAN_payloads.md).

## Traps

- A camera on USB reads about 3.1 V: that is the rail, not a cell.
- The webhook's path for **Uplink message** must be empty: TTN appends it to the Base URL, after
  `?token=`.
- An organisation token (`issue_lorawan_ingest_token`) works too, but goes with the next reset.
