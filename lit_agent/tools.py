import json

from .category_manager import (
    create_category,
    get_papers_in_category,
    get_uncategorized_papers,
    list_categories,
    manually_categorize,
    remove_from_category,
)
from .database import Paper, Tag, get_session
from .exporter import to_bibtex
from .search import get_paper_by_id, search_papers as arxiv_search


def search_papers_tool(query: str, max_results: int = 5) -> str:
    """Search arXiv for papers matching the query."""
    papers = arxiv_search(query, max_results)
    if not papers:
        return "No papers found."
    lines = []
    for i, p in enumerate(papers, 1):
        authors = ", ".join(p["authors"][:3])
        if len(p["authors"]) > 3:
            authors += " et al."
        lines.append(
            f"{i}. [{p['arxiv_id']}] {p['title']} ({p['year']}) — {authors}"
        )
    return "\n".join(lines)


def add_to_library(arxiv_id: str) -> str:
    """Add a paper to the library by its arXiv ID."""
    session = get_session()
    try:
        existing = session.query(Paper).filter_by(arxiv_id=arxiv_id).first()
        if existing:
            return f"Paper {arxiv_id} already in library (id={existing.id})."

        paper_data = get_paper_by_id(arxiv_id)
        if not paper_data:
            print(f"[DB] Paper not found on arXiv: {arxiv_id}", flush=True)
            return f"Paper {arxiv_id} not found on arXiv."

        paper = Paper(
            title=paper_data["title"],
            authors=json.dumps(paper_data["authors"]),
            abstract=paper_data["abstract"],
            year=paper_data["year"],
            venue=paper_data["venue"],
            doi=paper_data["doi"],
            arxiv_id=paper_data["arxiv_id"],
            pdf_url=paper_data["pdf_url"],
        )
        session.add(paper)
        session.commit()
        return f"Added: [{paper.id}] {paper.title} ({paper.year})"
    finally:
        session.close()


def list_library(tag: str = None, limit: int = 20) -> str:
    """List papers in the library, optionally filtered by tag."""
    session = get_session()
    try:
        query = session.query(Paper)
        if tag:
            query = query.join(Paper.tags).filter(Tag.name == tag)
        papers = query.order_by(Paper.created_at.desc()).limit(limit).all()
        if not papers:
            return "Library is empty."
        lines = []
        for p in papers:
            authors = json.loads(p.authors)[:2]
            author_str = ", ".join(authors)
            tags_str = ", ".join(t.name for t in p.tags) if p.tags else ""
            lines.append(
                f"[{p.id}] {p.title} ({p.year}) — {author_str}"
                + (f"  tags: {tags_str}" if tags_str else "")
            )
        return "\n".join(lines)
    finally:
        session.close()


def get_paper_detail(paper_id: int) -> str:
    """Get full details of a paper from the library."""
    session = get_session()
    try:
        paper = session.query(Paper).get(paper_id)
        if not paper:
            return f"Paper id={paper_id} not found."
        return json.dumps(
            {
                "id": paper.id,
                "title": paper.title,
                "authors": json.loads(paper.authors),
                "abstract": paper.abstract,
                "year": paper.year,
                "venue": paper.venue,
                "doi": paper.doi,
                "arxiv_id": paper.arxiv_id,
                "pdf_url": paper.pdf_url,
            },
            ensure_ascii=False,
            indent=2,
        )
    finally:
        session.close()


def search_library(query: str, limit: int = 10) -> str:
    """Full-text search across paper titles and abstracts in the library."""
    session = get_session()
    try:
        papers = (
            session.query(Paper)
            .filter(
                (Paper.title.contains(query)) | (Paper.abstract.contains(query))
            )
            .limit(limit)
            .all()
        )
        if not papers:
            return "No matching papers in library."
        lines = []
        for p in papers:
            lines.append(f"[{p.id}] {p.title} ({p.year})")
        return "\n".join(lines)
    finally:
        session.close()


def add_tag(paper_id: int, tag_name: str) -> str:
    """Tag a paper with a category label."""
    session = get_session()
    try:
        paper = session.query(Paper).get(paper_id)
        if not paper:
            return f"Paper id={paper_id} not found."
        tag = session.query(Tag).filter_by(name=tag_name).first()
        if not tag:
            tag = Tag(name=tag_name)
            session.add(tag)
            session.flush()
        if tag in paper.tags:
            return f"Paper {paper_id} already tagged with '{tag_name}'."
        paper.tags.append(tag)
        session.commit()
        return f"Tagged paper {paper_id} with '{tag_name}'."
    finally:
        session.close()


def export_bibtex(paper_ids: str) -> str:
    """Export papers from library as BibTeX. paper_ids is a comma-separated string."""
    session = get_session()
    try:
        ids = [int(x.strip()) for x in paper_ids.split(",")]
        papers = session.query(Paper).filter(Paper.id.in_(ids)).all()
        if not papers:
            return "No papers found for given IDs."
        return to_bibtex(papers)
    finally:
        session.close()


# ============================================================================
# Category folder tools (for agent use)
# ============================================================================


def list_categories_tool() -> str:
    """列出文献库中所有的分类夹（文件夹），包含每个分类夹的ID、名称和论文数量。"""
    cats = list_categories()
    if not cats:
        return "当前没有任何分类夹。请先创建分类夹（例如：人工智能、无人对抗、强化学习）。"
    lines = ["当前分类夹列表："]
    for c in cats:
        lines.append(
            f"  [{c['id']}] {c['name']} — {c['approved_count']} 篇论文"
            + (f"（{c['pending_count']} 篇待审核）" if c['pending_count'] else "")
        )
    return "\n".join(lines)


def get_uncategorized_papers_tool() -> str:
    """列出文献库中尚未分类到任何分类夹的论文。"""
    papers = get_uncategorized_papers()
    if not papers:
        return "所有论文都已分类到分类夹中。"
    lines = [f"未分类论文共 {len(papers)} 篇："]
    for p in papers:
        lines.append(f"  [{p['id']}] {p['title'][:80]} ({p['year']})")
    return "\n".join(lines)


def get_papers_in_category_tool(category_id: int) -> str:
    """查看某个分类夹中的所有论文。"""
    papers = get_papers_in_category(category_id, status="approved")
    if not papers:
        return f"分类夹 id={category_id} 中没有论文，或该分类夹不存在。"
    lines = [f"分类夹中共 {len(papers)} 篇论文："]
    for p in papers:
        authors = ", ".join(p["authors"][:2])
        lines.append(f"  [{p['id']}] {p['title'][:80]} ({p['year']}) — {authors}")
    return "\n".join(lines)


def add_paper_to_category_tool(paper_id: int, category_id: int) -> str:
    """将一篇论文添加到指定的分类夹中。需要提供论文编号和分类夹编号。"""
    return manually_categorize(paper_id, category_id)


def create_category_tool(name: str, description: str = "", color: str = "#6b7280") -> str:
    """Create a new category folder (分类夹) in the library."""
    return create_category(name, description, color)


def remove_paper_from_category_tool(paper_id: int, category_id: int) -> str:
    """从指定分类夹中移除一篇论文。"""
    return remove_from_category(paper_id, category_id)
