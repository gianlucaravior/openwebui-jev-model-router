# Installation

## Prerequisites

Before installing, ensure that:

- Your Open WebUI version is 0.9.1 or newer.
- `httpx` is available in the Open WebUI Python environment.
- You have a Cloudflare account ID and a token with **Workers AI Read** permission.
- You have at least one active Open WebUI chat model that can answer requests.
- You know the Open WebUI model IDs for the router entry point and fallback model.

If you use an AI Gateway, create or identify its gateway ID and ensure it permits the configured token and decision models.

## Add the Filter

1. Open Open WebUI and go to **Workspace** > **Functions**.
2. Create a new function of type **Filter**.
3. Paste the contents of `jev-semantic_router_filter.py`.
4. Save the function.
5. Record the function ID that Open WebUI assigns. Set the `filter_id` valve to exactly that value.

The filter ID is important: the router adds it to routed model metadata so the filter stays active after the request model changes.

## Prepare Models

1. Create or select a normal Open WebUI chat model preset to be the router entry point.
2. Do not configure JEV or Clef as the router entry model. They only decide the route and do not answer the user.
3. Add useful descriptions to every candidate response model.
4. Mark models that can process images with the Open WebUI Vision capability.
5. Enable this filter on the router entry preset and every model the router may select.

The router needs the filter on selected models too. Otherwise it cannot restore an existing route or detect a Voice Mode transition on later messages.

## Configure the Required Valves

In the filter's valve settings, configure at least:

| Valve | Required value |
| --- | --- |
| `router_entry_model_id` | The exact Open WebUI ID of the preset users select to start routed chats. |
| `filter_id` | The Filter Function ID assigned by your Open WebUI installation. |
| `fallback_model_id` | A reliable Open WebUI chat model ID. |
| `cloudflare_account_id` | Your Cloudflare account ID. |
| `cloudflare_api_token` | A raw Cloudflare API token with Workers AI Read permission. Do not prefix it with `Bearer `. |
| `text_decision_model` | Usually `typesafe/jev`. |
| `image_text_decision_model` | Usually `@cf/cloudflare/clef-flash`. |

Set `cloudflare_gateway_id` when the calls must use a specific Cloudflare AI Gateway. An empty value uses the `default` gateway.

For all options and defaults, see [Configuration Reference](configuration.md).

## Validate the Setup

Run these checks in separate new chats:

1. Send a text request using the router entry model. Confirm a response model is selected.
2. Send a follow-up text message. Confirm the same route is reused.
3. Start an image request. Confirm only vision-capable candidates can be selected.
4. Send a Voice Mode request, then switch to text or voice. Confirm the router makes a fresh selection.
5. Temporarily use invalid Cloudflare credentials. Confirm the configured fallback model answers instead.

Enable `debug` only while diagnosing a problem. It logs detailed operational information, but never the API token.
