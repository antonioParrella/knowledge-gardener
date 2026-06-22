"""
gemini_client.py — Backward-compatibility shim.

The Gemini logic moved to `providers/gemini.py`, and call sites now route through
`llm.py` (task-based provider selection + fallback). This module preserves the
old import surface (`gemini_simple`, `gemini_tool_loop`, `parse_json_response`)
for any remaining callers. Prefer importing `llm_simple` / `llm_tool_loop` from
`llm` directly, passing an explicit `task`.
"""

from llm import llm_simple, llm_tool_loop, parse_json_response  # noqa: F401


def gemini_simple(prompt: str, system: str = "", task: str = "clip") -> str:
    return llm_simple(prompt, system=system, task=task)


def gemini_tool_loop(prompt, system, tool_schema, tool_executor, task: str = "research", **kwargs):
    return llm_tool_loop(prompt, system, tool_schema, tool_executor, task=task, **kwargs)
