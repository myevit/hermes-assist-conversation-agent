# Hermes Assist Conversation Agent

<p align="center">
  <img src="assets/hermes-conversation-agent-logo.png" alt="Hermes Assist Conversation Agent logo" width="180">
</p>

![Hermes Assist Conversation Agent banner](assets/banner.svg)

Home Assistant custom conversation agent for routing Assist utterances to a local [Hermes Agent](https://github.com/NousResearch/hermes-agent) bridge.

## What this repository contains

- `custom_components/hermes_assist_conversation/` — the Home Assistant custom integration. This is the part HACS installs.
- `bridge/bridge.py` — an optional example HTTP bridge that forwards Home Assistant Assist requests to a locally installed Hermes Agent.

## Architecture

![Architecture diagram](assets/architecture.svg)

The integration and bridge communicate over HTTP:

- `GET /health` — bridge health check used during setup
- `POST /api/chat` — authenticated chat endpoint used by the conversation agent

## HACS installation

This repository must be **public** for HACS to use it.

1. In HACS, open **Custom repositories**.
2. Add this repository URL.
3. Select category **Integration**.
4. Install **Hermes Assist Conversation**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration → Hermes Assist Conversation**.
7. Enter your bridge URL.
8. Select `TARS Home` as the conversation agent in your Assist pipeline.

## Manual installation

Copy this directory into Home Assistant:

```text
custom_components/hermes_assist_conversation
```

Final Home Assistant paths should be:

```text
/config/custom_components/hermes_assist_conversation/manifest.json
/config/custom_components/hermes_assist_conversation/__init__.py
/config/custom_components/hermes_assist_conversation/config_flow.py
/config/custom_components/hermes_assist_conversation/conversation.py
/config/custom_components/hermes_assist_conversation/brand/icon.png
/config/custom_components/hermes_assist_conversation/brand/logo.png
```

Restart Home Assistant, then add the integration from Devices & services.

## Bridge setup

Run the bridge on the machine that has Hermes Agent installed. For the initial
private-network test, enable explicit test mode and inject the *internal*
Hermes Gateway key from 1Password. Home Assistant never receives this key.

Example test deployment:

```bash
export HERMES_ASSIST_TEST_MODE=1
export HERMES_ASSIST_PORT=8765
# HERMES_API_KEY is injected by `op run`, not written here.
python3 bridge/bridge.py
```

For a persistent deployment, run the bridge under systemd or another service manager and store secrets outside the repository.

Supported bridge environment variables:

- `HERMES_ASSIST_HOST` — default `127.0.0.1`; a non-loopback bind requires explicit test mode until channel authentication is implemented
- `HERMES_ASSIST_PORT` — default `8765`
- `HERMES_ASSIST_TEST_MODE` — must be `1` for the unauthenticated HA-channel test; do not use outside an isolated test network
- `HERMES_API_URL` — loopback Hermes Gateway URL, default `http://127.0.0.1:8642`
- `HERMES_API_KEY` — internal Gateway key injected from 1Password
- `HERMES_ASSIST_BRIDGE_KEY` / `HERMES_ASSIST_BRIDGE_KEY_FILE` — reserved for the post-test authenticated channel mode

## Security notes

- Do not commit API keys, Home Assistant long-lived access tokens, Hermes credential files, `.env` files, or local service secrets.
- Test mode deliberately omits authentication on the HA-to-bridge hop. Restrict it to a private test network and remove test mode before any permanent deployment.
- The bridge-to-Gateway hop remains bearer authenticated and targets only the `tars-home` profile.
- Production authentication will use the household's Authentik identity provider, not the temporary test-mode bypass.
- Review the `tars-home` API toolsets before exposing it to voice input.

## Copyright and license

This project is licensed under the MIT License. See `LICENSE` and `NOTICE.md`.
