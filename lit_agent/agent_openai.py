"""
Agent using OpenAI-compatible API (DeepSeek, OpenAI, OpenRouter, etc.)
Supports tool/function calling with the OpenAI message format.
"""

import json
import os

from openai import OpenAI

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

SYSTEM_PROMPT = """你是一个文献管理助手。帮助研究人员：

1. **搜索** arXiv 上的学术论文
2. **添加** 感兴趣的论文到个人文献库
3. **列出** 文献库中的论文
4. **查看** 论文的完整摘要和详情
5. **搜索** 本地文献库
6. **打标签** 分类管理论文
7. **导出** BibTeX 引用
8. **管理分类夹** 查看、创建、归类论文到分类夹文件夹中

## 分类夹（文件夹）管理

你可以使用以下工具管理分类夹：
- `list_categories` — 列出所有分类夹（文件夹）及其ID和论文数量
- `create_category` — 创建新的分类夹（需提供名称，可选描述和颜色）
- `get_uncategorized_papers` — 查看哪些论文尚未分类
- `get_papers_in_category` — 查看某个分类夹中的所有论文
- `add_paper_to_category` — 将论文添加到指定分类夹（需提供论文ID和分类夹ID）
- `remove_paper_from_category` — 从分类夹中移除论文

**分类流程**：
1. 先用 `list_categories` 查看已有的分类夹
2. 用 `get_uncategorized_papers` 查看未分类的论文
3. 阅读论文标题和摘要，判断应归入哪个分类夹
4. 用 `add_paper_to_category` 逐篇归类
5. 最后报告归类结果

规则：
- 用户问到研究主题时，先搜索 arXiv，展示结果，再建议添加
- 添加论文后，主动建议打标签和归类
- 结构化解读：背景问题 → 方法 → 核心发现 → 意义
- 当用户要求整理或分类论文时，必须使用分类夹工具，而不是凭记忆编造"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "在 arXiv 上搜索学术论文。返回带编号的论文列表，包含 arXiv ID、标题、作者、年份。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，例如 'attention mechanism in transformers'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回数量（默认 5）",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_library",
            "description": "将论文添加到文献库。参数为 arXiv ID。",
            "parameters": {
                "type": "object",
                "properties": {
                    "arxiv_id": {
                        "type": "string",
                        "description": "arXiv ID，例如 '1706.03762'",
                    },
                },
                "required": ["arxiv_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_library",
            "description": "列出文献库中的论文，可按标签过滤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "标签名（可选）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大返回数量（默认 20）",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_detail",
            "description": "获取论文的完整详情，包括摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "integer",
                        "description": "文献库中的论文编号",
                    },
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_library",
            "description": "在本地文献库中按关键词搜索（标题+摘要）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大返回数量（默认 10）",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_tag",
            "description": "为论文添加分类标签。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "integer",
                        "description": "文献库中的论文编号",
                    },
                    "tag_name": {
                        "type": "string",
                        "description": "标签名，例如 'NLP'、'CV'、'RL'",
                    },
                },
                "required": ["paper_id", "tag_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_bibtex",
            "description": "生成论文的 BibTeX 引用。paper_ids 用逗号分隔，例如 '1,2,3'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_ids": {
                        "type": "string",
                        "description": "逗号分隔的论文编号，例如 '1,2,3'",
                    },
                },
                "required": ["paper_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_categories",
            "description": "列出文献库中所有的分类夹（文件夹），包含每个分类夹的ID、名称和论文数量。在分类之前必须先调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_category",
            "description": "创建一个新的分类夹（文件夹）。当用户要求新建分类夹，或者分类时发现没有合适的分类夹时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "分类夹名称，例如 '大语言模型'、'计算机视觉'",
                    },
                    "description": {
                        "type": "string",
                        "description": "分类夹的描述（可选）",
                    },
                    "color": {
                        "type": "string",
                        "description": "分类夹的十六进制颜色标识，例如 '#3b82f6'（默认 '#6b7280'）",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_uncategorized_papers",
            "description": "列出文献库中尚未归类到任何分类夹的论文。在分类之前应先调用此工具了解待分类论文。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_papers_in_category",
            "description": "查看某个分类夹中的所有论文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category_id": {
                        "type": "integer",
                        "description": "分类夹的ID编号",
                    },
                },
                "required": ["category_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_paper_to_category",
            "description": "将一篇论文添加到指定的分类夹中。需要提供论文编号和分类夹编号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "integer",
                        "description": "论文编号（文献库中的ID）",
                    },
                    "category_id": {
                        "type": "integer",
                        "description": "分类夹编号（从 list_categories 获取）",
                    },
                },
                "required": ["paper_id", "category_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_paper_from_category",
            "description": "从指定分类夹中移除一篇论文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "integer",
                        "description": "论文编号",
                    },
                    "category_id": {
                        "type": "integer",
                        "description": "分类夹编号",
                    },
                },
                "required": ["paper_id", "category_id"],
            },
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

# API provider configurations
PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4",
    },
}


def run_agent_openai(
    api_key: str,
    user_message: str = None,
    messages: list = None,
    provider: str = "deepseek",
    model: str = None,
    base_url: str = None,
) -> tuple[str, list]:
    """Compatibility entrypoint; all execution uses the budgeted server runtime."""
    from .runtime import Guard, Model, conversation
    if provider != "deepseek" or base_url or model:
        raise ValueError("请使用服务端 LIT_BASE_URL / LIT_MODEL 配置模型。")
    context = list(messages or [])
    if user_message:
        context.append({"role": "user", "content": user_message})
    guard = Guard()
    backend = Model(guard)
    try:
        return conversation(context, backend, guard)
    finally:
        backend.close()
