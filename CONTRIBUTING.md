# Contributing

Contributions that improve routing accuracy, compatibility, documentation, tests, and operational safety are welcome.

## Before You Start

- Search [existing issues](https://github.com/gianlucaravior/openwebui-jev-model-router/issues) before opening a new one.
- Open an issue first for substantial changes so the approach can be discussed.
- Keep each pull request focused on one problem.
- Never include API tokens, Cloudflare account details, private conversations, uploaded images, or other sensitive data.

## Report a Bug

Open a [bug report](https://github.com/gianlucaravior/openwebui-jev-model-router/issues/new?template=bug_report.md) when the current filter behaves unexpectedly.

Include:

- Open WebUI version and deployment method.
- Filter version from the file header.
- Python version and whether `httpx` is installed.
- Decision backends in use, such as JEV or Clef Flash.
- A redacted valve configuration. Replace every secret with `<redacted>`.
- Exact steps to reproduce, expected result, and actual result.
- Redacted relevant logs, including the exception and stack trace when available.

Do not report security vulnerabilities in public issues. Read [SECURITY.md](SECURITY.md) instead.

## Request a Feature

Open a [feature request](https://github.com/gianlucaravior/openwebui-jev-model-router/issues/new?template=feature_request.md) and explain:

- The user problem you are trying to solve.
- Why the existing configuration cannot solve it.
- The proposed behaviour and any Open WebUI or Cloudflare dependencies.
- Compatibility or privacy implications.

## Submit a Pull Request

1. Fork the repository and create a focused branch.
2. Make the smallest change that solves the issue.
3. Update documentation when user-visible behaviour, valves, prerequisites, or troubleshooting changes.
4. Run the checks below.
5. Open a pull request that links the relevant issue and explains how the change was validated.

## Local Checks

The filter depends on Open WebUI modules at runtime, so a complete standalone execution is not available in this repository. At minimum, verify Python syntax:

```bash
python -m py_compile jev-semantic_router_filter.py
```

For behaviour changes, test the modified path in a non-production Open WebUI instance. Cover text routing, route persistence, fallback behaviour, and image or Voice Mode paths when your change affects them.

## Documentation Contributions

Documentation is part of the product. Keep documentation in English, use short factual sentences, and update the relevant page in `docs/` rather than expanding the README with implementation details. Check relative links and code examples before opening a pull request.

## Code Style

- Preserve the existing Python style and type annotations.
- Prefer small, direct changes over broad refactors.
- Do not log secrets or full authorization headers.
- Keep fallback behaviour safe when external decision services fail.
