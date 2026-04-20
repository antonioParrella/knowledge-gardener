"""
researcher.py — Agentic research pipeline.

Triggered when the watchdog detects a note in _triggers/ with research: true.

Pipeline:
  1. Parse topic, depth, and optional seed URLs from trigger note frontmatter
  2. Pull prior context from existing MOCs
  3. Run Gemini tool loop (search_web / fetch_url / save_source)
  4. Write research summary to Research/
  5. Index the summary note into MOCs
  6. Mark trigger note as done
"""

from pathlib import Path

from config import RESEARCH_PATH, load_prompt
from notes import read_note, write_note, today
from gemini_client import gemini_tool_loop
from web_tools import TOOL_SCHEMA, execute_tool, reset_source_counter
from indexer import index_note, get_prior_context


def process_research_trigger(path: Path):
    """
    Full research pipeline for a trigger note.
    Skips silently if not a valid unprocessed research trigger.
    """
    try:
        fm, _ = read_note(path)
    except Exception as e:
        print(f"[research] Could not read {path.name}: {e}")
        return

    # Guard: only process active research triggers
    if fm.get("research") is not True:
        return

    topic = fm.get("topic", path.stem.replace("research - ", "").strip())
    depth = fm.get("depth", "standard")
    seed_urls = fm.get("urls", []) or []
    output_name = fm.get("output", f"Research - {topic}.md")

    print(f"[research] Starting: '{topic}' (depth: {depth})")

    # Reset source counter for this run
    reset_source_counter()

    # Pull prior knowledge from the vault
    prior_context = get_prior_context(topic)
    if prior_context:
        print(f"[research] Found prior context in knowledge base.")

    # Build the research prompt
    prompt_parts = [f"Research this topic in depth:\n\n**{topic}**"]

    if prior_context:
        prompt_parts.append(prior_context)

    if seed_urls:
        urls_list = "\n".join(f"- {u}" for u in seed_urls)
        prompt_parts.append(f"## Seed URLs to include:\n{urls_list}")

    prompt_parts.append(
        f"Depth: {depth}\n"
        "Use search_web to find information, fetch_url to read full articles, "
        "and save_source for any genuinely valuable sources you find."
    )

    prompt = "\n\n".join(prompt_parts)

    # Load prompts from files
    system_prompt = load_prompt("research_system")
    
    # Run the agentic research loop
    summary = gemini_tool_loop(
        prompt=prompt,
        system=system_prompt,
        tool_schema=TOOL_SCHEMA,
        tool_executor=execute_tool,
    )

    # Write the research note
    output_path = RESEARCH_PATH / output_name
    write_note(
        output_path,
        frontmatter={
            "generated": True,
            "topic": topic,
            "depth": depth,
            "date": today(),
            "tags": [w.lower() for w in topic.split()[:4]],
        },
        body=summary,
    )
    print(f"[research] Written: {output_name}")

    # Index the research note
    index_note(
        note_title=output_name.replace(".md", ""),
        note_path=output_path,
        summary=summary[:300],
        tags=[w.lower() for w in topic.split()[:4]],
    )

    # Mark trigger as done so watchdog doesn't reprocess
    fm["research"] = "done"
    fm["completed"] = today()
    write_note(path, fm, f"Research completed. See [[{output_name.replace('.md', '')}]].")

    print(f"[research] Done: '{topic}'")
