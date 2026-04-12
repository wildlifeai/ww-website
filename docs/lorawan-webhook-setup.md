# LoRaWAN Webhook Setup Guide

How to connect your LoRaWAN network server (TTN or Chirpstack) to the Wildlife Watcher API for real-time device monitoring.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [The Things Network (TTN) v3](#the-things-network-ttn-v3)
- [Chirpstack v4](#chirpstack-v4)
- [Testing Your Webhook](#testing-your-webhook)
- [Understanding the Data](#understanding-the-data)
- [Mobile App Integration](#mobile-app-integration)
- [Troubleshooting](#troubleshooting)

---

## Overview

Wildlife Watcher cameras can transmit real-time telemetry (battery level, SD card status, AI detections) via LoRaWAN. The data flow is:

```
Camera → LoRa Radio → Gateway → Network Server → Webhook → Wildlife Watcher API
                                                                      │
                                                                      ▼
                                                              Supabase Database
                                                                      │
                                                                      ▼
                                                    Mobile App (live updates via Realtime)
```

The API processes each uplink and:

1. **Parses** the binary payload (battery, SD usage, model output)
2. **Matches** the device to its record in the database by LoRaWAN EUI
3. **Finds** the active deployment for that device
4. **Stores** the raw message and parsed data in Supabase
5. **Broadcasts** automatically to connected mobile apps via Supabase Realtime

---

## Prerequisites

- A Wildlife Watcher camera with LoRaWAN connectivity
- A LoRaWAN gateway within range
- An account on TTN (The Things Network) or a Chirpstack instance
- The Wildlife Watcher API deployed and accessible (see [Deployment Guide](deployment-guide.md))
- Your API's webhook URL: `https://api.wildlifewatcher.ai/api/lorawan/webhook/ttn` (or `/chirpstack`)

---

## The Things Network (TTN) v3

### Step 1: Register Your Device

If you haven't already, register your Wildlife Watcher camera on TTN:

1. Go to [TTN Console](https://console.thethingsnetwork.org)
2. Select or create an **Application**
3. Click **Register end device**
4. Enter your device's:
   - **DevEUI** (printed on the camera's LoRa module, 16 hex characters)
   - **AppKey** (from the camera's firmware configuration)
5. Click **Register**

### Step 2: Note the Webhook Secret

Generate a secure webhook secret. You'll use this in both TTN and your API configuration:

```bash
# Generate a random 32-character secret
openssl rand -hex 16
# Example output: a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

Set this in your API's environment:

```bash
# In .env or Render environment variables
LORAWAN_TTN_WEBHOOK_SECRET=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

### Step 3: Create the Webhook Integration

1. In TTN Console, go to your **Application** → **Integrations** → **Webhooks**
2. Click **+ Add webhook**
3. Choose **Custom webhook**
4. Configure:

   | Field | Value |
   |-------|-------|
   | **Webhook ID** | `wildlife-watcher` |
   | **Webhook format** | `JSON` |
   | **Base URL** | `https://api.wildlifewatcher.ai/api/lorawan/webhook/ttn` |
   | **Downlink API key** | _(leave empty)_ |

5. Under **Additional headers**, add:

   | Header Name | Header Value |
   |-------------|-------------|
   | `X-Webhook-Secret` | Your secret from Step 2 |

6. Under **Enabled event types**, check:
   - ✅ **Uplink message**
   - _(Uncheck all others unless you want to process joins, etc.)_

7. Click **Add webhook**

### Step 4: Verify the Device EUI is in Supabase

For the API to match incoming messages to devices, ensure your device is registered in Supabase:

1. Go to Supabase Dashboard → Table Editor → `devices`
2. Find or create your device record
3. Set the `lorawan_device_eui` field to the device's EUI (e.g., `0004A30B001F9ACB`)

---

## Chirpstack v4

### Step 1: Register Your Device

1. In Chirpstack web UI, go to **Applications** → select your app
2. Click **Add device**
3. Enter:
   - **Device name**: `ww-camera-01`
   - **Device EUI**: Your camera's EUI
   - **Device profile**: Select your LoRaWAN device profile
4. Click **Submit**

### Step 2: Create the HTTP Integration

1. Go to **Applications** → your app → **Integrations** → **HTTP**
2. Click **+ Add**
3. Configure:

   | Field | Value |
   |-------|-------|
   | **Payload encoding** | `JSON` |
   | **Event endpoint URL(s)** | `https://api.wildlifewatcher.ai/api/lorawan/webhook/chirpstack` |

4. Under **Headers**, add:

   | Key | Value |
   |-----|-------|
   | `X-Webhook-Secret` | Your webhook secret |

5. Under **Events**, enable:
   - ✅ **Uplink**

6. Click **Submit**

### Step 3: Set the Secret in API Config

```bash
LORAWAN_CHIRPSTACK_WEBHOOK_SECRET=your-secret-here
```

---

## Testing Your Webhook

### Send a Test Uplink

You can test the webhook without a physical device by sending a simulated uplink:

**TTN Format:**

```bash
curl -X POST https://api.wildlifewatcher.ai/api/lorawan/webhook/ttn \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your-secret-here" \
  -d '{
    "end_device_ids": {
      "device_id": "test-device",
      "dev_eui": "0004A30B001F9ACB",
      "application_ids": {
        "application_id": "wildlife-watcher"
      }
    },
    "uplink_message": {
      "frm_payload": "S0o=",
      "f_port": 1
    },
    "received_at": "2026-04-12T03:00:00Z"
  }'
```

The `frm_payload` is base64-encoded. `S0o=` decodes to bytes `[75, 42]`, which means:
- Battery: 75%
- SD Card: 42% used

**Chirpstack Format:**

```bash
curl -X POST https://api.wildlifewatcher.ai/api/lorawan/webhook/chirpstack \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your-secret-here" \
  -d '{
    "deviceInfo": {
      "devEui": "0004A30B001F9ACB",
      "deviceName": "test-device"
    },
    "data": "S0o=",
    "fPort": 1
  }'
```

### Expected Response

```json
{
  "data": {
    "device_eui": "0004A30B001F9ACB",
    "battery_level": 75.0,
    "sd_card_used_capacity": 42.0,
    "model_output": null,
    "raw_payload_hex": "4b2a",
    "received_at": "2026-04-12T03:00:00Z"
  },
  "meta": {
    "request_id": "..."
  }
}
```

### Encoding Test Payloads

To create test payloads with different values:

```python
import base64

# Battery 85%, SD 60%, no model output
payload = bytes([85, 60])
print(base64.b64encode(payload).decode())  # → "VTw="

# Battery 100%, SD 10%, with JSON model output
import json
model_data = json.dumps({"detection": "bird", "confidence": 0.87}).encode()
payload = bytes([100, 10]) + model_data
print(base64.b64encode(payload).decode())
```

---

## Understanding the Data

### Payload Format

The Wildlife Watcher firmware sends a compact binary frame over LoRaWAN:

```
┌──────────┬──────────┬───────────────────────────────────┐
│  Byte 0  │  Byte 1  │  Bytes 2+  (optional)             │
│ Battery  │ SD Card  │  Model Output                     │
│  (0-100) │  (0-100) │  (JSON or binary)                 │
└──────────┴──────────┴───────────────────────────────────┘
```

| Byte | Field | Type | Range | Description |
|------|-------|------|-------|-------------|
| 0 | Battery Level | uint8 | 0–100 | Battery charge percentage |
| 1 | SD Card Usage | uint8 | 0–100 | SD card capacity used (%) |
| 2+ | Model Output | JSON/binary | variable | AI inference results (if any) |

### Model Output Examples

**Object detection:**

```json
{
  "detection": "person",
  "confidence": 0.95,
  "bbox": [10, 20, 100, 150]
}
```

**Multiple detections:**

```json
{
  "detections": [
    {"class": "bird", "confidence": 0.92},
    {"class": "person", "confidence": 0.45}
  ]
}
```

**No model output** (telemetry only):

Payload is just 2 bytes — battery and SD card status.

### Database Storage

Each uplink creates two records:

| Table | Fields | Purpose |
|-------|--------|---------|
| `lorawan_messages` | `device_eui`, `device_id`, `deployment_id`, `raw_payload` | Raw audit trail |
| `lorawan_parsed_messages` | `lorawan_message_id`, `device_id`, `battery_level`, `sd_card_used_capacity`, `model_output` | Parsed data for queries |

---

## Mobile App Integration

The mobile app receives live LoRaWAN data via **Supabase Realtime**. When the API inserts into `lorawan_parsed_messages`, Supabase automatically broadcasts the row to subscribed clients.

### How It Works

1. **API receives webhook** → inserts into `lorawan_parsed_messages`
2. **Supabase Realtime** detects the INSERT and broadcasts to all subscribers
3. **Mobile app** listening on the Realtime channel receives the update immediately
4. **UI updates** with new battery level, SD status, and detections

### Mobile App Subscription (React Native)

```javascript
import { supabase } from './supabaseClient';

// Subscribe to live updates for a specific device
const channel = supabase
  .channel('lorawan-updates')
  .on(
    'postgres_changes',
    {
      event: 'INSERT',
      schema: 'public',
      table: 'lorawan_parsed_messages',
      filter: `device_id=eq.${deviceId}`,
    },
    (payload) => {
      console.log('New LoRaWAN data:', payload.new);
      updateBatteryLevel(payload.new.battery_level);
      updateSDStatus(payload.new.sd_card_used_capacity);
    }
  )
  .subscribe();

// Cleanup
return () => supabase.removeChannel(channel);
```

---

## Troubleshooting

### Webhook returns 401

**Cause:** Secret mismatch between network server and API.

**Fix:** Verify the `X-Webhook-Secret` header matches the configured environment variable.

```bash
# Check what the API expects
echo $LORAWAN_TTN_WEBHOOK_SECRET
```

### Webhook returns 422

**Cause:** Payload format doesn't match expected schema.

**Fix:** Ensure your network server is sending the correct format. TTN should use "JSON" format, not "Protobuf". Check the API logs for the exact validation error.

### Data appears in API response but not in Supabase

**Cause:** The `lorawan_messages` or `lorawan_parsed_messages` table doesn't exist, or RLS policies are blocking the service-role insert.

**Fix:**
1. Create the tables in Supabase (based on the migration schema)
2. Verify the `SUPABASE_SERVICE_ROLE_KEY` is correct (service-role bypasses RLS)

### Device not matched (device_found: false in logs)

**Cause:** The device's EUI in the `devices` table doesn't match the uplink.

**Fix:** Ensure `devices.lorawan_device_eui` exactly matches the EUI sent in the webhook (all uppercase, no separators).

### Mobile app not receiving live updates

**Cause:** Realtime is not enabled on the `lorawan_parsed_messages` table.

**Fix:**
1. Supabase Dashboard → Database → Replication
2. Toggle ON for `lorawan_parsed_messages`
3. Restart the mobile app's Supabase client

### Messages are arriving but battery_level is null

**Cause:** The firmware payload might be empty or have a different format.

**Fix:** Check `raw_payload_hex` in the response. If it's empty or unexpected, the firmware may need updating. The expected format is: `[battery_byte, sd_byte, optional_model_data...]`.
