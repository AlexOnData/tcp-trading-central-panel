"""TCP AI assistant package.

Hosts the Anthropic-Claude integration for the ``/api/ask`` HTTP trigger:

- :mod:`tcp.ai.prompts` — the long-form, prompt-cached system context
  enumerating every view, proc, and function the model may reference,
  plus the locale rules and few-shot examples.
- :mod:`tcp.ai.anthropic_client` — the thin SDK wrapper that issues a
  single ``claude-haiku-4-5`` call per question and parses the
  ``tool_use`` response into a typed :class:`AskAnswer`.

The two modules are intentionally separated so the prompt body can be
unit-tested for token-count regressions independently of the SDK call.
"""
