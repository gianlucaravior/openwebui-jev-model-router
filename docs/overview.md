# Overview

## What This Filter Is

JEV Model Router is an Open WebUI **inlet filter**. It intercepts a request before Open WebUI sends it to a chat provider, selects one of your existing Open WebUI models, and rewrites the request to use that model.

It is not a chat model and it does not generate the assistant response. JEV, Clef, and Clef Flash only evaluate the routing choice. The selected Open WebUI model continues to use its own provider connection, credentials, AI Gateway configuration, system prompt, tools, and knowledge configuration.

## Routing Lifecycle

1. A user starts a new chat with the configured router-entry model.
2. The filter gathers eligible active Open WebUI models.
3. For an image request, the candidate list contains only models marked as vision capable.
4. The filter sends recent conversation content, interaction mode, selected request tools, and compact candidate descriptions to Cloudflare JEV or Clef.
5. The evaluator returns one candidate choice and a qualitative context score.
6. The filter maps that choice back to the real Open WebUI model ID and rebuilds the request with that model's configuration.
7. An invisible marker is added to the assistant turn so later turns can restore the same route.
8. A normal follow-up turn reuses the persisted route without another Cloudflare decision request.

## When the Router Re-evaluates

The filter requests a new routing decision when:

- A new conversation begins through the router-entry model.
- Voice Mode changes between text and voice.
- A new image is attached and the prior route is not vision capable.

Growing conversation length by itself does not trigger re-routing. This is intentional: evaluating context need on every turn would make every message slower and add Cloudflare requests.

## Candidate Eligibility

A model is eligible only when it is active and all applicable checks pass:

- It is not the router-entry model.
- It is not a filter pipeline model.
- It is included in `allowed_models` when that list is non-empty.
- It is not listed in `banned_models`.
- It has vision capability when the current request includes an image.

## Choosing a Decision Backend

| Request type | Recommended decision model | Why |
| --- | --- | --- |
| Text only | `typesafe/jev` | Structured text decision evaluation. |
| Text only | `@cf/cloudflare/clef` or `@cf/cloudflare/clef-flash` | Alternative structured decision backends. |
| Image and text | `@cf/cloudflare/clef` or `@cf/cloudflare/clef-flash` | Clef can inspect embedded image data. |

JEV cannot inspect image pixels. Clef requires image bytes embedded as base64 or data URLs; remote image URLs are not forwarded because the Cloudflare API requires image bytes.

## What Is Preserved

When a route is applied, the filter preserves or applies the selected model's configuration where Open WebUI exposes it:

- Model ID and model metadata.
- System prompt.
- Tools, built-in features, and function-calling mode.
- Knowledge collections and their files.
- Filter IDs, so this filter remains active on routed follow-up turns.
- Conversion between OpenAI-style and Ollama-style request payloads when the source and target provider differ.

See [configuration](configuration.md) for the complete valve reference and [usage](usage.md) for day-to-day operation.
