"""
Category folder management — CRUD, AI auto-categorization, approval workflow.
"""

import json

from .database import Category, Paper, PaperCategory, Tag, get_session

# ============================================================================
# Category CRUD
# ============================================================================


def create_category(name: str, description: str = "", color: str = "#6b7280") -> str:
    """Create a new category folder. Returns status message."""
    session = get_session()
    try:
        existing = session.query(Category).filter_by(name=name).first()
        if existing:
            return f"分类夹 '{name}' 已存在。"
        cat = Category(name=name, description=description, color=color)
        session.add(cat)
        session.commit()
        return f"已创建分类夹：{name}"
    finally:
        session.close()


def list_categories() -> list[dict]:
    """List all categories with paper counts."""
    session = get_session()
    try:
        cats = session.query(Category).order_by(Category.name).all()
        result = []
        for c in cats:
            approved_count = (
                session.query(PaperCategory)
                .filter_by(category_id=c.id, status="approved")
                .count()
            )
            pending_count = (
                session.query(PaperCategory)
                .filter_by(category_id=c.id, status="pending")
                .count()
            )
            result.append({
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "color": c.color,
                "approved_count": approved_count,
                "pending_count": pending_count,
            })
        return result
    finally:
        session.close()


def rename_category(category_id: int, new_name: str) -> str:
    session = get_session()
    try:
        cat = session.query(Category).get(category_id)
        if not cat:
            return f"分类夹 id={category_id} 不存在。"
        conflict = session.query(Category).filter_by(name=new_name).first()
        if conflict and conflict.id != category_id:
            return f"分类夹 '{new_name}' 已存在。"
        old = cat.name
        cat.name = new_name
        session.commit()
        return f"已将 '{old}' 重命名为 '{new_name}'。"
    finally:
        session.close()


def delete_category(category_id: int) -> str:
    session = get_session()
    try:
        cat = session.query(Category).get(category_id)
        if not cat:
            return f"分类夹 id={category_id} 不存在。"
        name = cat.name
        session.delete(cat)
        session.commit()
        return f"已删除分类夹：{name}"
    finally:
        session.close()


# ============================================================================
# Paper-Category queries
# ============================================================================


def get_papers_in_category(category_id: int, status: str = "approved") -> list[dict]:
    """Get all papers in a category, filtered by approval status."""
    from sqlalchemy.orm import selectinload

    session = get_session()
    try:
        pcs = (
            session.query(PaperCategory)
            .options(
                selectinload(PaperCategory.paper).selectinload(Paper.tags),
                selectinload(PaperCategory.category),
            )
            .filter_by(category_id=category_id, status=status)
            .order_by(PaperCategory.created_at.desc())
            .all()
        )
        papers = []
        for pc in pcs:
            p = pc.paper
            authors = json.loads(p.authors)
            tags_str = ", ".join(t.name for t in p.tags)
            papers.append({
                "id": p.id,
                "title": p.title,
                "year": p.year,
                "authors": authors[:3],
                "tags": tags_str,
                "arxiv_id": p.arxiv_id,
                "paper_category_id": pc.id,
                "status": pc.status,
                "ai_reason": pc.ai_reason,
                "suggested_by": pc.suggested_by,
            })
        return papers
    finally:
        session.close()


def get_pending_suggestions() -> list[dict]:
    """Get all AI suggestions awaiting user approval."""
    session = get_session()
    try:
        pcs = (
            session.query(PaperCategory)
            .filter_by(status="pending")
            .order_by(PaperCategory.created_at.desc())
            .all()
        )
        result = []
        for pc in pcs:
            p = pc.paper
            cat = pc.category
            authors = json.loads(p.authors)
            result.append({
                "paper_category_id": pc.id,
                "paper_id": p.id,
                "title": p.title,
                "year": p.year,
                "authors": ", ".join(authors[:3]),
                "category_name": cat.name,
                "ai_reason": pc.ai_reason,
                "created_at": pc.created_at.isoformat() if pc.created_at else "",
            })
        return result
    finally:
        session.close()


def get_uncategorized_papers() -> list[dict]:
    """Get papers that have no approved or pending category assignment."""
    session = get_session()
    try:
        # Papers without any approved/pending PaperCategory entry
        from sqlalchemy import select

        categorized_ids = (
            select(PaperCategory.paper_id)
            .where(PaperCategory.status.in_(["approved", "pending"]))
        )
        papers = (
            session.query(Paper)
            .filter(~Paper.id.in_(categorized_ids))
            .order_by(Paper.created_at.desc())
            .all()
        )
        result = []
        for p in papers:
            authors = json.loads(p.authors)
            result.append({
                "id": p.id,
                "title": p.title,
                "year": p.year,
                "authors": ", ".join(authors[:3]),
                "abstract": p.abstract[:200] if p.abstract else "",
                "arxiv_id": p.arxiv_id,
            })
        return result
    finally:
        session.close()


# ============================================================================
# Approval workflow
# ============================================================================


def approve_paper_category(paper_category_id: int) -> str:
    """Approve an AI-suggested category assignment."""
    session = get_session()
    try:
        pc = session.query(PaperCategory).get(paper_category_id)
        if not pc:
            return f"记录 id={paper_category_id} 不存在。"
        pc.status = "approved"
        session.commit()
        return f"已批准：'{pc.paper.title[:50]}...' → {pc.category.name}"
    finally:
        session.close()


def reject_paper_category(paper_category_id: int) -> str:
    """Reject an AI-suggested category assignment."""
    session = get_session()
    try:
        pc = session.query(PaperCategory).get(paper_category_id)
        if not pc:
            return f"记录 id={paper_category_id} 不存在。"
        pc.status = "rejected"
        session.commit()
        return f"已拒绝：'{pc.paper.title[:50]}...' → {pc.category.name}"
    finally:
        session.close()


def manually_categorize(paper_id: int, category_id: int) -> str:
    """Manually assign a paper to a category (auto-approved)."""
    session = get_session()
    try:
        existing = (
            session.query(PaperCategory)
            .filter_by(paper_id=paper_id, category_id=category_id)
            .first()
        )
        if existing:
            if existing.status == "approved":
                return "该论文已在此分类夹中。"
            else:
                existing.status = "approved"
                existing.suggested_by = "manual"
                session.commit()
                return "已将该论文移入分类夹。"
        pc = PaperCategory(
            paper_id=paper_id,
            category_id=category_id,
            status="approved",
            suggested_by="manual",
        )
        session.add(pc)
        session.commit()
        return f"已添加论文到分类夹。"
    finally:
        session.close()


def remove_from_category(paper_id: int, category_id: int) -> str:
    """Remove a paper from a category."""
    session = get_session()
    try:
        pc = (
            session.query(PaperCategory)
            .filter_by(paper_id=paper_id, category_id=category_id)
            .first()
        )
        if not pc:
            return "该论文不在此分类夹中。"
        session.delete(pc)
        session.commit()
        return "已从分类夹中移除。"
    finally:
        session.close()


# ============================================================================
# AI Auto-Categorization
# ============================================================================


CATEGORIZE_PROMPT = """You are a research paper classifier. Your task is to assign papers to the most appropriate category folders.

Available category folders:
{categories}

Papers to classify:
{papers}

Rules:
- Assign each paper to the 1-2 most relevant categories
- If a paper doesn't fit any category, leave it unassigned
- Provide a brief reason (in Chinese, under 30 chars) explaining why each paper belongs to a category

Return ONLY valid JSON in this exact format:
{{
  "suggestions": [
    {{"paper_id": 1, "category_id": 2, "reason": "论文内容涉及无人机集群对抗博弈"}},
    ...
  ]
}}"""


def ai_suggest_categories(
    api_key: str, provider: str = "deepseek", base_url: str = None
) -> str:
    """AI reads uncategorized papers and suggests category assignments.
    Returns a summary report string."""
    session = get_session()
    try:
        # Get uncategorized papers
        uncategorized = get_uncategorized_papers()
        if not uncategorized:
            return "没有需要分类的论文，所有论文都已归类。"

        # Get all categories
        cats = session.query(Category).all()
        if not cats:
            return "请先创建至少一个分类夹（例如：人工智能、无人对抗、强化学习）。"

        # Assign uncategorized papers back to session for later querying
        session.close()

        cat_lines = []
        for c in cats:
            cat_lines.append(f"  id={c.id}: {c.name}")
        cat_text = "\n".join(cat_lines)

        papers_text = json.dumps(uncategorized, ensure_ascii=False, indent=2)
        prompt = CATEGORIZE_PROMPT.format(categories=cat_text, papers=papers_text)

        print(
            f"[CATEGORIZE] Sending {len(uncategorized)} papers to AI for categorization...",
            flush=True,
        )

        # Reopen session
        session2 = get_session()
        try:
            if provider == "anthropic":
                result = _categorize_via_anthropic(api_key, prompt)
            else:
                result = _categorize_via_openai(api_key, prompt, provider, base_url)

            # Parse result
            try:
                json_str = result
                if "```json" in result:
                    json_str = result.split("```json")[1].split("```")[0]
                elif "```" in result:
                    json_str = result.split("```")[1].split("```")[0]
                data = json.loads(json_str.strip())
                suggestions = data.get("suggestions", [])
            except (json.JSONDecodeError, IndexError) as e:
                print(f"[CATEGORIZE] Failed to parse AI response: {e}", flush=True)
                return f"AI 返回结果解析失败：{e}\n\n原始响应:\n{result[:500]}"

            created = 0
            details = []

            for s in suggestions:
                paper_id = s["paper_id"]
                category_id = s["category_id"]
                reason = s.get("reason", "")

                # Check for existing pending/approved entry
                existing = (
                    session2.query(PaperCategory)
                    .filter_by(paper_id=paper_id, category_id=category_id)
                    .first()
                )
                if existing:
                    continue

                pc = PaperCategory(
                    paper_id=paper_id,
                    category_id=category_id,
                    status="pending",
                    suggested_by="ai",
                    ai_reason=reason,
                )
                session2.add(pc)
                paper = session2.query(Paper).get(paper_id)
                cat = session2.query(Category).get(category_id)
                if paper and cat:
                    details.append(
                        f"  [{paper_id}] {paper.title[:40]}... → {cat.name}（{reason}）"
                    )
                created += 1

            session2.commit()

            report = f"## AI 分类建议完成\n\n"
            report += f"- 扫描未分类论文：**{len(uncategorized)}** 篇\n"
            report += f"- 生成分类建议：**{created}** 条\n"
            report += f"- 状态：等待审核\n\n"
            report += f"### 建议详情\n\n"
            report += "\n".join(details) if details else "无新建议。"
            report += f"\n\n请在「审核队列」中批准或拒绝这些建议。"

            print(
                f"[CATEGORIZE] Done: {created} suggestions created", flush=True
            )
            return report
        finally:
            session2.close()
    finally:
        pass  # session already closed above


# ============================================================================
# AI helpers
# ============================================================================


def _categorize_via_openai(
    api_key: str, prompt: str, provider: str, base_url: str = None
) -> str:
    from openai import OpenAI

    provider_configs = {
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        },
        "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "model": "openai/gpt-4o",
        },
    }
    cfg = provider_configs.get(
        provider,
        {
            "base_url": base_url or "https://api.deepseek.com",
            "model": "deepseek-chat",
        },
    )

    client = OpenAI(api_key=api_key, base_url=base_url or cfg["base_url"])
    model = cfg["model"]

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


def _categorize_via_anthropic(api_key: str, prompt: str) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
