# TARS Home Assist Conversation

<p align="center">
  <img src="assets/hermes-conversation-agent-logo.png" alt="TARS Home Assist Conversation logo" width="180">
</p>

![TARS Home Assist architecture](assets/banner.svg)

A Home Assistant custom conversation agent that routes Assist requests to a **restricted, persistent Hermes `tars-home` profile**. Home Assistant remains the voice/UI channel and smart-home capability provider; the TARS profile owns the agent identity, memory, and safety policy.

> **Status — private-network test integration.** The Home Assistant → bridge hop currently uses an explicit unauthenticated test mode. It is suitable only for an isolated local/loopback test deployment. Authentik service-to-service enforcement is planned before a permanent deployment.

## What is installed where

| Component | Purpose | Installed by this repository? |
|---|---|---|
| `custom_components/hermes_assist_conversation/` | Home Assistant conversation integration and config flow | Yes — HACS/manual install |
| `bridge/bridge.py` | Narrow HTTP proxy from HA Assist to Hermes Gateway | Included, but you operate it |
| Hermes Gateway API | Runs the isolated `tars-home` profile | No — existing Hermes deployment |
| `tars-home` profile | TARS identity, memory, Home Assistant tools, policy | No — site-specific Hermes profile |

The bridge deliberately **does not import or run Hermes**. It forwards one conversational request to the profile-scoped OpenAI-compatible Responses endpoint:

```text
Home Assistant Assist / satellite
  → Hermes Assist Conversation integration
  → local bridge: /api/chat
  → local Hermes Gateway: /p/tars-home/v1/responses
  → restricted tars-home profile
  → Home Assistant / Music Assistant capabilities
  → Assist speech response
```

## Security model

### TARS Home profile boundary

`/p/tars-home/...` must route to a dedicated profile with only the capabilities intended for household voice control:

- Home Assistant and Music Assistant access
- memory/context needed for a continuous household agent
- no shell/terminal, filesystem, browser/computer control, GitHub, 1Password, TrueNAS, UniFi, cron, code execution, messaging, or unrelated MCP servers

Keep security-sensitive operations—locks, doors, garage, alarm/security, purchases, messaging, and irreversible actions—behind spoken confirmation in the TARS policy. Routine lighting, climate, media, and timers can remain direct.

### Credentials

- Store every real token/key only in **1Password**.
- Inject runtime secrets at process start; never commit keys, `.env`, `auth.json`, Home Assistant long-lived tokens, or service files containing secrets.
- The Hermes Gateway bearer key stays inside the bridge process. It is **not** entered into the Home Assistant config flow.
- The bridge accepts only an HTTP loopback URL for the Hermes Gateway.

### Current test mode

`HERMES_ASSIST_TEST_MODE=1` intentionally omits authentication on the Home Assistant → bridge hop. During the verified test deployment, bind the bridge to `127.0.0.1` and configure Home Assistant with the same loopback URL. Do not expose test mode on a shared LAN, WAN, reverse proxy, or public hostname.

The current Home Assistant integration does not yet send the future bridge bearer/OIDC credentials. Therefore **do not simply turn off test mode and assume production auth works**. Complete the planned Authentik channel-authentication work first.

## Prerequisites

1. Home Assistant **2024.8+** with HACS (or a manual custom-components installation).
2. Hermes Agent with a `tars-home` profile.
3. A scoped `API_SERVER_KEY` available to the `tars-home` Gateway process through 1Password-backed secret injection.
4. API server listener configuration for the TARS profile:

   ```text
   API_SERVER_ENABLED=true
   API_SERVER_HOST=127.0.0.1
   API_SERVER_PORT=8642
   ```

5. A process supervisor for both the profile Gateway and bridge. In the Hermes s6 container, `hermes --profile tars-home gateway start` registers/starts the profile Gateway. The bridge requires its own deployment-managed sidecar/service.

> The bridge has no built-in boot-time installer. A runtime-created s6 sidecar survives bridge crashes but is not recreated by Hermes' current profile-gateway reconciler after a full container replacement. Treat boot-persistent bridge deployment as an explicit infrastructure task.

## HACS installation

1. In Home Assistant, open **HACS → Integrations → Custom repositories**.
2. Add `myevit/hermes-assist-conversation-agent` as category **Integration**.
3. Download **Hermes Assist Conversation**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration**.
6. Add **Hermes Assist Conversation**.
7. Use a descriptive name such as **TARS Home**.
8. Enter the bridge URL. For the verified same-host test setup:

   ```text
   http://127.0.0.1:8766
   ```

The config flow calls `GET /health`. It refuses a bridge that reports `ok: false`.

### Manual installation

Copy the integration directory into Home Assistant:

```text
custom_components/hermes_assist_conversation
```

Expected files include:

```text
/config/custom_components/hermes_assist_conversation/manifest.json
/config/custom_components/hermes_assist_conversation/__init__.py
/config/custom_components/hermes_assist_conversation/config_flow.py
/config/custom_components/hermes_assist_conversation/conversation.py
```

Restart Home Assistant, then use the same config-flow steps above.

## Assist pipeline setup

After the integration loads, it creates a conversation entity named like:

```text
conversation.tars_home
```

In **Settings → Voice assistants**, edit the intended Assist pipeline and choose **TARS Home** as its conversation agent.

The Assist popup may still display the pipeline name—for example, **Home Assistant**—in its selector. That label is not proof that the pipeline is using the built-in Home Assistant conversation agent. Verify the pipeline's **conversation agent** is `conversation.tars_home`.

For the first test, keep the existing STT/TTS/wake-word settings unchanged and change only the conversation agent. This isolates the new TARS route from audio-pipeline changes.

## Bridge runtime configuration

The bridge reads the following variables:

| Variable | Default | Meaning |
|---|---:|---|
| `HERMES_ASSIST_HOST` | `127.0.0.1` | Bridge listener. Keep loopback for test mode. |
| `HERMES_ASSIST_PORT` | `8765` | Bridge listener port. |
| `HERMES_ASSIST_TEST_MODE` | `0` | Set to `1` only for the temporary unauthenticated test channel. |
| `HERMES_API_URL` | `http://127.0.0.1:8642` | Local TARS Gateway API URL; loopback only. |
| `HERMES_API_KEY` | — | Injected Gateway bearer key; required. |
| `HERMES_ASSIST_TIMEOUT` | `90` | Upstream request timeout in seconds. |
| `HERMES_ASSIST_BRIDGE_KEY` / `HERMES_ASSIST_BRIDGE_KEY_FILE` | — | Reserved for the post-test authenticated channel. |

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Bridge configuration/readiness signal for HA setup. It does **not** probe the Hermes Gateway. |
| `POST /api/chat` | Conversation request forwarded to `/p/tars-home/v1/responses`. |

The bridge preserves a bounded conversation ID and limits request/response sizes. It avoids logging headers, request bodies, query strings, or upstream bearer material.

## Verification checklist

Verify in this order after installing or changing services:

1. **TARS Gateway service** is running:

   ```bash
   hermes --profile tars-home gateway status
   ```

2. **Gateway API** is listening locally on the configured port:

   ```bash
   curl --fail http://127.0.0.1:8642/health
   ```

3. **Bridge health** returns `"ok": true`:

   ```bash
   curl --fail http://127.0.0.1:8766/health
   ```

4. In Home Assistant, the config entry is **loaded** and `conversation.tars_home` exists.
5. The selected Assist pipeline uses **TARS Home** as its conversation agent.
6. Send a harmless request through Assist, for example:

   ```text
   Hello
   ```

7. Verify a read-only household request, for example:

   ```text
   What is Eva's room temperature?
   ```

8. Before testing media playback, verify that the intended room has a real, available Music Assistant player. Do not assume an unassigned or future Sonos player exists.

## Troubleshooting

### “TARS is temporarily unavailable.”

This means Home Assistant reached the bridge, but the bridge could not obtain a valid TARS response. Check the path in this order:

```text
1. tars-home Gateway process
2. Gateway API listener
3. bridge process
4. Home Assistant integration/pipeline
```

A healthy bridge `/health` response alone is insufficient: it checks bridge configuration, not whether the Gateway is running.

Typical recovery for an s6-managed Hermes container:

```bash
hermes --profile tars-home gateway start
hermes --profile tars-home gateway status
```

Then retest the Gateway and bridge health endpoints. If the Gateway is down after a clean `--replace` takeover, the bridge will return this message until the profile Gateway is started again.

### The Assist selector says “Home Assistant”

That can be the pipeline title. Open the pipeline settings and verify its conversation-agent field points to **TARS Home** / `conversation.tars_home`.

### Home Assistant cannot add the integration

- Confirm the bridge URL is reachable from the Home Assistant runtime.
- Confirm `GET /health` returns HTTP 200 with JSON `{"ok": true, ...}`.
- In a same-host loopback deployment, use the same network namespace/host loopback address for both HA and bridge. Do not substitute a LAN address while test mode has no channel authentication.

### Gateway starts but media does not play in a room

The voice route can be healthy while the media target is absent. Check that:

- the player exists and is available in Home Assistant;
- it is assigned to the intended room/area;
- Music Assistant can search the requested content;
- the request names an actual available target.

## Production hardening roadmap

Before treating this as a permanent household control channel:

1. Replace `HERMES_ASSIST_TEST_MODE=1` with the dedicated Authentik service client:
   - client: `ha-tars-channel`
   - grant: `client_credentials`
   - audience: `tars-home`
   - scope: `tars.home.voice`
2. Validate issuer/JWKS, expiry, audience, client identity, and scope at the TARS channel boundary.
3. Make bridge deployment boot-persistent through a supported Hermes/deployment sidecar mechanism.
4. Remove any temporary or stale bridge listeners.
5. Exercise sensitive-action confirmation behavior with a controlled test before household-wide voice use.
6. Add/assign the actual room satellite and Music Assistant player before declaring voice-to-room playback complete.

## Development

Focused bridge tests are in `tests/test_tars_home_bridge.py`:

```bash
.venv/bin/python -m pytest -q tests/test_tars_home_bridge.py
.venv/bin/python -m compileall -q bridge custom_components tests
git diff --check
```

## License

MIT. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
