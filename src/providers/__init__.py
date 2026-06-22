"""LLM provider implementations behind a common interface.

Each provider exposes `simple()` and `tool_loop()` matching the signatures the
pipeline relies on. The router in `llm.py` selects a provider+model per task and
walks a fallback chain. See AGENTS.md for the routing rationale.
"""
