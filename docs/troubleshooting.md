# Troubleshooting

## No Route on a New Chat

Check the following:

1. The selected Open WebUI model ID exactly matches `router_entry_model_id`.
2. The filter is enabled for the router-entry model.
3. At least one active response model is eligible.
4. `allowed_models` does not exclude every candidate.
5. `banned_models` does not exclude every candidate.

Enable `debug` temporarily and inspect the Open WebUI server logs for the candidate count and model IDs. Do not share logs containing private prompts or internal model details publicly.

## Follow-up Turns Do Not Stay Routed

Set `filter_id` to the actual Filter Function ID assigned by Open WebUI. Also attach the filter to every response model that may be selected. The filter uses a hidden routing marker and routed metadata to restore the chosen model on later turns.

## Voice Mode Does Not Re-route

Confirm that the filter is attached to response models as well as the router-entry model. Verify your Open WebUI version sends `body.features.voice` before inlet filters run. The filter also accepts `metadata.voice` and `metadata.audio` as compatibility hints.

## Cloudflare Authentication or Gateway Error

Verify that:

- `cloudflare_account_id` is the correct account ID.
- `cloudflare_api_token` is the raw token, without `Bearer `.
- The token has Workers AI Read permission.
- Workers AI is available for the account.
- The configured AI Gateway ID exists and permits the token and decision model.
- The selected JEV or Clef model is supported by your Cloudflare account.

## An Image Request Does Not Route

Ensure that target response models are marked with the Vision capability in Open WebUI. Set `image_text_decision_model` to Clef or Clef Flash. Clef needs embedded image bytes, not remote image URLs; inspect how your Open WebUI upload/storage configuration represents attachments in the request.

## A Model Is Never Selected

Check that the model is active, not the router-entry model, not a filter pipeline, and not blocked by `allowed_models` or `banned_models`. For image requests, confirm Vision capability. Improve the model description so its intended tasks, tools, speed, and context fit are distinguishable from competing candidates.

## The Target System Prompt Is Missing

The filter copies the selected model's `params.system` into the outgoing message list. Confirm the selected Open WebUI preset has its system prompt configured there. If the source and target prompts differ, the target prompt replaces the source prompt only when the source prompt came from the source model metadata.

## A Routing Failure Uses the Wrong Model

Confirm `fallback_model_id` contains an existing, active Open WebUI model ID. The fallback is applied only when the configured ID is available in the Open WebUI model state. If it is empty or unavailable, the original incoming model remains selected.

## Reporting a Bug

If the problem remains, open a [bug report](https://github.com/gianlucaravior/openwebui-jev-model-router/issues/new?template=bug_report.md). Include Open WebUI and filter versions, a redacted valve configuration, the exact reproduction steps, expected and actual result, and relevant redacted logs. See [CONTRIBUTING.md](../CONTRIBUTING.md) for the full reporting guidance.
