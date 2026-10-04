# Configuration Reference

Configure these values in the Filter Function's valve settings in Open WebUI.

## Routing Valves

| Valve | Default | Description |
| --- | --- | --- |
| `router_entry_model_id` | Empty | Exact Open WebUI model ID that begins a new routed conversation. |
| `filter_id` | `semantic_router` | The installed Filter Function ID. This is inserted into routed metadata to keep the filter active. |
| `fallback_model_id` | Empty | Open WebUI chat model ID used when routing fails. Empty leaves the incoming model unchanged. |
| `allowed_models` | `[]` | Optional allowlist of response-model IDs. When set, only listed models are candidates. |
| `banned_models` | `[]` | Response-model IDs that must never be selected. A banned model remains excluded even if it is allowlisted. |

## Cloudflare Valves

| Valve | Default | Description |
| --- | --- | --- |
| `text_decision_model` | `typesafe/jev` | Decision model for requests without images. Supported values: `typesafe/jev`, `@cf/cloudflare/clef`, and `@cf/cloudflare/clef-flash`. |
| `image_text_decision_model` | `@cf/cloudflare/clef-flash` | Decision model for requests with images. Use a Clef model to evaluate image data. |
| `cloudflare_account_id` | Empty | Cloudflare account ID used in the Workers AI API URL. |
| `cloudflare_api_token` | Empty | Secret Cloudflare token with Workers AI Read permission. Enter only the token value. |
| `cloudflare_gateway_id` | Empty | Optional AI Gateway ID. An empty value sends `default` as the gateway ID. |
| `request_timeout_seconds` | `20.0` | Timeout for a Cloudflare decision request. Allowed range: 1 to 120 seconds. |

## Behaviour and Diagnostics

| Valve | Default | Description |
| --- | --- | --- |
| `router_instructions` | Routing guidance text | Retained configuration guidance. The current compact JEV/Clef request relies on candidate descriptions and does not duplicate this text as a chat prompt. |
| `show_reasoning` | `false` | Shows a compact model-selection details block in the chat. It contains the selected model ID and evaluator confidence/context score. |
| `status` | `true` | Shows Open WebUI status events while routing and after selection. |
| `debug` | `false` | Enables diagnostic logging. The filter does not log the API token. Disable it in normal production use. |

## Write Effective Model Descriptions

Candidate descriptions are the main source of truth for routing. Describe each model's strengths, limitations, tools, cost or speed characteristics, and expected context capacity.

You can add a qualitative context label:

| Label | Meaning |
| --- | --- |
| `S` | Short context, up to approximately 100K tokens. |
| `M` | Medium context, up to approximately 250K tokens. |
| `L` | Long context, up to approximately 1M tokens. |

Example:

> Fast, low-cost general assistant for short factual questions, rewriting, and simple planning. Supports native tool calling and web search. Not intended for complex coding or long multi-step reasoning. Context: S.

These labels are routing guidance, not enforced token limits. Retrieval output and provider-side prompt additions can change actual context consumption.

## Tools, Knowledge, and Function Calling

The routing decision receives tools selected for the request when Open WebUI exposes them in the inlet payload. Candidate information includes each model's configured tool IDs, enabled built-ins/default features, and function-calling mode. A tool ID without a description is still useful, but clear model descriptions make tool matching more reliable.

After a route is selected, the filter applies the selected model's configured tools and knowledge collections to the outgoing Open WebUI request. Models configured for native function calling may omit file attachments because of Open WebUI payload compatibility.

## Secret Handling

`cloudflare_api_token` is rendered as a password-style valve and is sent only from the Open WebUI server to Cloudflare. Some Open WebUI versions mask a saved value when reopening the valve dialog. A blank-looking masked input does not necessarily mean that the saved secret was removed.

Never include tokens in screenshots, logs, public issues, or pull requests.
