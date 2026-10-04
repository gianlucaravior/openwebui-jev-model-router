# Using the Router

## Start a Routed Chat

1. Select the configured router-entry model in Open WebUI.
2. Start a new conversation with a normal request.
3. The filter chooses an eligible response model.
4. Continue the conversation normally. Follow-up turns use the persisted route unless a re-routing condition occurs.

If `status` is enabled, Open WebUI shows that the router is analysing the request and then displays the selected model name. If `show_reasoning` is enabled, the chat also shows a compact details block with the selected model ID and decision metadata.

## Text Requests

For a text-only request, the router uses `text_decision_model`. The standard configuration uses `typesafe/jev`. JEV receives the recent non-system conversation, current interaction mode, selected request tools, and compact candidate descriptions.

Example requests:

```text
Explain this error message and give me a minimal Python fix.
```

```text
Compare these three architecture options and recommend one for a high-traffic API.
```

The quality of the selected route depends on how accurately response-model descriptions state coding, reasoning, tool, speed, and context capabilities.

## Image Requests

When the latest user message includes an image, the router limits candidates to models marked as Vision capable in Open WebUI. It uses `image_text_decision_model`, which should be Clef or Clef Flash.

Clef receives embedded image bytes when they are present in the request. Remote image URLs cannot be sent to Clef. If Open WebUI provides only a remote URL, routing fails and the configured fallback path applies.

## Voice Mode

Open WebUI sets `body.features.voice` before inlet filters run. The router persists whether the selected route was made for voice or text.

- An unchanged voice conversation keeps its route.
- Switching from text to Voice Mode, or the reverse, triggers a new decision.
- Recorded dictation transcribed into the input field is ordinary text; it cannot be reliably distinguished from typing.

For voice-oriented candidates, mention response speed and concise-answer behaviour in their descriptions.

## Fallback Behaviour

The router uses `fallback_model_id` when it cannot complete a valid decision, including these cases:

- Missing Cloudflare account ID or API token.
- Network timeouts or Cloudflare API errors.
- Unsupported decision model configuration.
- An invalid evaluator response or a choice outside the candidate list.
- No eligible response models.
- Failure while restoring a persisted route.

The fallback must be an Open WebUI chat model, never a JEV or Clef evaluator model. If no valid fallback is configured, the filter leaves the incoming model unchanged.

## Operational Recommendations

- Keep the candidate pool small and intentional. Use `allowed_models` for predictable production routing.
- Give every candidate a concise, factual description and context label where appropriate.
- Use a stable, capable fallback model.
- Test routing after adding, deleting, deactivating, or renaming Open WebUI models.
- Keep `debug` disabled except during diagnosis.
- Review Cloudflare data handling before routing sensitive conversations or images.
