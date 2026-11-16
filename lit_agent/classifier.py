"""
AI-powered auto-classification of papers in the library.
Analyzes titles + abstracts and assigns tags automatically.
"""

import json
import sys

from .database import Paper, Tag, get_session

CLASSIFY_PROMPT = """You are a research paper classifier. Given a list of papers (each with id, title, abstract), assign 1-3 relevant tags to each paper.

Available tags should be from these standard categories:
- Machine Learning, Deep Learning, Reinforcement Learning, NLP, Computer Vision, Robotics,
- Adversarial Learning, Security, Optimization, Game Theory, Graph Neural Networks,
- Survey, Medical, Bioinformatics, Autonomous Systems, Multi-Agent, UAV/Drone,
- Transfer Learning, Federated Learning, Uncertainty, Explainable AI, Ethics

But you can also create NEW tags if none of the above fit. Keep tags short (1-3 words max).

Return ONLY valid JSON in this exact format:
{
  "paper_id": ["tag1", "tag2"],
  ...
}

Example: {"1": ["NLP", "Deep Learning"], "2": ["Reinforcement Learning", "Game Theory"]}

Here are the papers to classify:"""


def auto_classify_papers(api_key: str, provider: str = "deepseek", base_url: str = None) -> str:
    """Classify all papers in the library using AI. Returns a summary report."""
    session = get_session()
    try:
        papers = session.query(Paper).all()
        if not papers:
            return "文献库为空，请先添加论文。"

        # Build paper list for the prompt
        paper_list = []
        for p in papers:
            authors = json.loads(p.authors)
            author_str = ", ".join(authors[:3])
            abstract_short = p.abstract[:300] if p.abstract else "N/A"
            paper_list.append({
                "id": p.id,
                "title": p.title,
                "abstract": abstract_short,
                "existing_tags": [t.name for t in p.tags],
            })

        papers_json = json.dumps(paper_list, ensure_ascii=False, indent=2)
        prompt = f"{CLASSIFY_PROMPT}\n{papers_json}"

        print(f"[CLASSIFY] Sending {len(paper_list)} papers to AI for classification...", flush=True)

        # Call DeepSeek API
        if provider == "anthropic":
            result = _classify_via_anthropic(api_key, prompt)
        else:
            result = _classify_via_openai(api_key, prompt, provider, base_url)

        # Parse result and apply tags
        try:
            # Extract JSON from the response (it might be wrapped in markdown)
            json_str = result
            if "```json" in result:
                json_str = result.split("```json")[1].split("```")[0]
            elif "```" in result:
                json_str = result.split("```")[1].split("```")[0]
            tag_map = json.loads(json_str.strip())
        except (json.JSONDecodeError, IndexError) as e:
            print(f"[CLASSIFY] Failed to parse AI response: {e}", flush=True)
            print(f"[CLASSIFY] Raw response: {result[:500]}", flush=True)
            return f"AI 返回结果解析失败：{e}\n\n原始响应:\n{result[:500]}"

        # Apply tags
        applied = 0
        skipped = 0
        new_tags = set()
        details = []

        for paper_id_str, tag_names in tag_map.items():
            paper_id = int(paper_id_str)
            paper = session.query(Paper).get(paper_id)
            if not paper:
                continue

            for tag_name in tag_names:
                tag_name = tag_name.strip()
                if not tag_name:
                    continue
                # Check if already tagged
                if any(t.name == tag_name for t in paper.tags):
                    skipped += 1
                    continue
                # Create or get tag
                existing_tag = session.query(Tag).filter_by(name=tag_name).first()
                if not existing_tag:
                    existing_tag = Tag(name=tag_name)
                    session.add(existing_tag)
                    session.flush()
                    new_tags.add(tag_name)
                paper.tags.append(existing_tag)
                applied += 1

            tag_list = ", ".join(tag_names)
            details.append(f"  [{paper_id}] {paper.title[:50]}... → {tag_list}")

        session.commit()

        # Build report
        report = f"## AI 自动分类完成\n\n"
        report += f"- 扫描论文：**{len(paper_list)}** 篇\n"
        report += f"- 新打标签：**{applied}** 个\n"
        report += f"- 已存在跳过：**{skipped}** 个\n"
        if new_tags:
            report += f"- 新创建标签：{', '.join(f'`{t}`' for t in sorted(new_tags))}\n"
        report += f"\n### 分类详情\n\n"
        report += "\n".join(details)

        print(f"[CLASSIFY] Done: {applied} tags applied, {skipped} skipped", flush=True)
        return report
    finally:
        session.close()


def _classify_via_openai(api_key: str, prompt: str, provider: str, base_url: str = None) -> str:
    from openai import OpenAI

    provider_configs = {
        "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
        "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
        "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o"},
    }
    cfg = provider_configs.get(provider, {"base_url": base_url or "https://api.deepseek.com", "model": "deepseek-chat"})

    client = OpenAI(api_key=api_key, base_url=base_url or cfg["base_url"])
    model = cfg["model"]

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": prompt},
        ],
        max_tokens=4096,
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


def _classify_via_anthropic(api_key: str, prompt: str) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
