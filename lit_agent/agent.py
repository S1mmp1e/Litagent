from anthropic import Anthropic

from .tools import (
    add_paper_to_category_tool,
    add_tag,
    add_to_library,
    create_category_tool,
    export_bibtex,
    get_paper_detail,
    get_papers_in_category_tool,
    get_uncategorized_papers_tool,
    list_categories_tool,
    list_library,
    remove_paper_from_category_tool,
    search_library,
    search_papers_tool,
)

SYSTEM_PROMPT = """You are a research literature management assistant. You help researchers:

1. **Search** for academic papers on arXiv by topic or keyword.
2. **Add** interesting papers to their personal library for later reference.
3. **List** and browse papers currently in the library.
4. **Retrieve** full details and abstracts of specific papers.
5. **Search** the local library across titles and abstracts.
6. **Tag** papers with categories (e.g., NLP, CV, RL, Systems).
7. **Export** selected papers as BibTeX for use in LaTeX documents.
8. **Manage category folders** — browse, organize, and assign papers to category folders.

## Category Folder Management

You have tools to manage category folders (分类夹):
- `list_categories` — List all category folders with their IDs and paper counts. Always call this FIRST.
- `create_category` — Create a new category folder (requires name, optional description and color).
- `get_uncategorized_papers` — List papers not yet assigned to any category.
- `get_papers_in_category` — Browse papers inside a specific category folder.
- `add_paper_to_category` — Assign a paper to a category folder (requires paper_id + category_id).
- `remove_paper_from_category` — Remove a paper from a category folder.

**Classification workflow**:
1. Call `list_categories` to see what folders exist
2. Call `get_uncategorized_papers` to see what needs classifying
3. Read each paper's title/abstract (use `get_paper_detail`), then assign it to the best-matching folder
4. Report the final results

Guidelines:
- When a user asks about a research topic, always search arXiv first, present the top results, then offer to add relevant ones to the library.
- When summarizing a paper from its abstract, structure it as: (1) Background/Problem, (2) Method/Approach, (3) Key Findings, (4) Significance.
- Proactively suggest tags and category folders when adding new papers.
- When the user asks to classify or organize papers, you MUST use the category tools — do not fabricate results.
- If the user asks a research question, search both arXiv AND the local library."""

TOOLS = [
    {
        "name": "search_papers",
        "description": "Search arXiv for academic papers. Returns a numbered list with arxiv IDs, titles, authors, and years.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. 'attention mechanism in transformers'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_to_library",
        "description": "Add a paper to the user's personal library by its arXiv ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "arxiv_id": {
                    "type": "string",
                    "description": "arXiv ID, e.g. '1706.03762'",
                },
            },
            "required": ["arxiv_id"],
        },
    },
    {
        "name": "list_library",
        "description": "List papers in the user's library, optionally filtered by a tag.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "Optional tag name to filter by",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 20)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_paper_detail",
        "description": "Get full details of a specific paper from the library, including the complete abstract.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "integer",
                    "description": "The library ID of the paper",
                },
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "search_library",
        "description": "Search the local library by keyword across titles and abstracts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword or phrase",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_tag",
        "description": "Tag a paper with a category label (e.g., 'NLP', 'CV', 'RL', 'Systems').",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "integer",
                    "description": "Paper library ID",
                },
                "tag_name": {
                    "type": "string",
                    "description": "Tag name, e.g. 'NLP', 'CV', 'RL'",
                },
            },
            "required": ["paper_id", "tag_name"],
        },
    },
    {
        "name": "export_bibtex",
        "description": "Generate BibTeX for papers in the library. paper_ids should be comma-separated, e.g. '1,2,3'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_ids": {
                    "type": "string",
                    "description": "Comma-separated paper IDs, e.g. '1,2,3'",
                },
            },
            "required": ["paper_ids"],
        },
    },
    {
        "name": "list_categories",
        "description": "List all category folders in the library with their IDs, names, and paper counts. Always call this first before classifying papers.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_category",
        "description": "Create a new category folder (分类夹) in the library. Use this when the user wants to create a new folder or when no suitable folder exists for a paper being classified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Category folder name, e.g. 'Large Language Models', 'Computer Vision'",
                },
                "description": {
                    "type": "string",
                    "description": "Optional description of the category folder",
                },
                "color": {
                    "type": "string",
                    "description": "Optional hex color code, e.g. '#3b82f6' (default: '#6b7280')",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_uncategorized_papers",
        "description": "List all papers not yet assigned to any category folder. Call this to discover papers that need classification.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_papers_in_category",
        "description": "Browse papers inside a specific category folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category_id": {
                    "type": "integer",
                    "description": "The category folder ID",
                },
            },
            "required": ["category_id"],
        },
    },
    {
        "name": "add_paper_to_category",
        "description": "Assign a paper to a category folder. Requires the paper ID and category ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "integer",
                    "description": "The paper's library ID",
                },
                "category_id": {
                    "type": "integer",
                    "description": "The category folder ID (from list_categories)",
                },
            },
            "required": ["paper_id", "category_id"],
        },
    },
    {
        "name": "remove_paper_from_category",
        "description": "Remove a paper from a category folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "integer",
                    "description": "The paper's library ID",
                },
                "category_id": {
                    "type": "integer",
                    "description": "The category folder ID",
                },
            },
            "required": ["paper_id", "category_id"],
        },
    },
]

TOOL_FUNCTIONS = {
    "search_papers": search_papers_tool,
    "add_to_library": add_to_library,
    "list_library": list_library,
    "get_paper_detail": get_paper_detail,
    "search_library": search_library,
    "add_tag": add_tag,
    "export_bibtex": export_bibtex,
    "list_categories": list_categories_tool,
    "create_category": create_category_tool,
    "get_uncategorized_papers": get_uncategorized_papers_tool,
    "get_papers_in_category": get_papers_in_category_tool,
    "add_paper_to_category": add_paper_to_category_tool,
    "remove_paper_from_category": remove_paper_from_category_tool,
}


def run_agent(
    api_key: str,
    user_message: str = None,
    messages: list = None,
    model: str = "claude-sonnet-4-6",
) -> tuple[str, list]:
    """Run the agent with tool-use loop. Pass messages= to maintain conversation context."""
    client = Anthropic(api_key=api_key)
    if not messages:
        messages = [{"role": "user", "content": user_message}]
    elif user_message is not None:
        messages.append({"role": "user", "content": user_message})

    while True:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Separate text and tool_use blocks
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b for b in response.content if b.type == "text"]

        if not tool_calls:
            final_text = "\n".join(b.text for b in text_blocks)
            messages.append({"role": "assistant", "content": final_text})
            return final_text, messages

        # Convert content blocks to plain dicts for SDK 0.100+ compatibility
        assistant_content = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        messages.append({"role": "assistant", "content": assistant_content})

        # Execute each tool and collect results
        tool_results = []
        for tc in tool_calls:
            func = TOOL_FUNCTIONS.get(tc.name)
            if func:
                try:
                    result = func(**tc.input)
                except Exception as e:
                    result = f"Error executing {tc.name}: {e}"
            else:
                result = f"Unknown tool: {tc.name}"
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                }
            )

        messages.append({"role": "user", "content": tool_results})
