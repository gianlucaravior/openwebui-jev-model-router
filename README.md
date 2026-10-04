# JEV Model Router for Open WebUI

An Open WebUI inlet filter that uses Cloudflare JEV or Clef to select the best configured Open WebUI chat model for a request. The selected Open WebUI model, not JEV or Clef, generates the final answer.

## What It Does

- Routes a new chat from one router-entry model to an eligible response model.
- Uses model descriptions, configured tools, vision support, conversation context, and text or Voice Mode to make the decision.
- Keeps the selected route for ordinary follow-up turns to avoid a decision request on every message.
- Re-evaluates when Voice Mode changes or a new image requires vision support.
- Preserves the selected model's system prompt, tools, knowledge collections, metadata, and provider-specific request format.
- Uses a configured Open WebUI model as a safe fallback when routing cannot complete.

## Quick Start

1. In Open WebUI, add `jev-semantic_router_filter.py` as a **Filter Function**.
2. Create or choose a normal Open WebUI model preset as the router entry point.
3. Enable the filter for the router entry model and for all models that the router can select.
4. Set `router_entry_model_id`, `filter_id`, Cloudflare credentials, decision models, and a fallback model in the filter valves.
5. Add accurate descriptions to every eligible response model.
6. Start a new chat using the router entry model.

See the full [installation guide](docs/installation.md) before deploying the filter.

## Documentation

- [Overview and routing lifecycle](docs/overview.md)
- [Installation](docs/installation.md)
- [Configuration reference](docs/configuration.md)
- [Using and operating the router](docs/usage.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Contributing and reporting bugs](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Requirements

- Open WebUI 0.9.1 or later.
- The `httpx` Python package in the Open WebUI runtime.
- A Cloudflare account and API token with **Workers AI Read** permission.
- Active Open WebUI chat models to use as routing candidates.
- Cloudflare JEV or Clef access. Use Clef or Clef Flash for requests that include images.

## Important Privacy Note

The filter sends the recent conversation content, selected tools, and eligible model descriptions to Cloudflare for the routing decision. For image requests, it can also send embedded image bytes to Clef. Review your Cloudflare data-handling settings before using this filter with sensitive conversations.

## Credits

Thank you to [@Haervwe](https://github.com/Haervwe) and the [Open WebUI Tools](https://github.com/Haervwe/open-webui-tools) project for providing an excellent example of clear, practical Open WebUI tool documentation. This project applies that approachable documentation style to an Open WebUI router with Cloudflare JEV and Clef support.

## Support

Please use [GitHub Issues](https://github.com/gianlucaravior/openwebui-jev-model-router/issues) for reproducible bugs and feature requests. Read [CONTRIBUTING.md](CONTRIBUTING.md) first, and do not post tokens, private prompts, or security vulnerabilities in public issues.
