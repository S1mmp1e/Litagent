import xml.etree.ElementTree as ET

import httpx

ARXIV_API = "https://export.arxiv.org/api/query"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def _parse_entry(entry: ET.Element) -> dict:
    title = entry.find("atom:title", NS).text.strip().replace("\n", " ")
    authors = [a.find("atom:name", NS).text for a in entry.findall("atom:author", NS)]
    abstract = entry.find("atom:summary", NS).text.strip().replace("\n", " ")
    arxiv_id = entry.find("atom:id", NS).text.split("/abs/")[-1]

    pdf_url = ""
    for link in entry.findall("atom:link", NS):
        if link.attrib.get("title") == "pdf":
            pdf_url = link.attrib.get("href", "")

    doi_el = entry.find("arxiv:doi", NS)
    doi = doi_el.text if doi_el is not None else ""

    published = entry.find("atom:published", NS).text
    year = int(published[:4])

    journal_el = entry.find("arxiv:journal_ref", NS)
    venue = journal_el.text if journal_el is not None else ""

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "arxiv_id": arxiv_id,
        "pdf_url": pdf_url,
        "doi": doi,
        "year": year,
        "venue": venue,
    }


def search_papers(query: str, max_results: int = 10) -> list[dict]:
    if not query.strip() or not 1 <= max_results <= 30 or len(query) > 2000:
        raise ValueError("搜索词不能为空，返回数量应在 1 到 30 之间。")
    params = {"search_query": f"all:{query}", "max_results": max_results}
    resp = httpx.get(ARXIV_API, params=params, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    entries = root.findall("atom:entry", NS)
    return [_parse_entry(e) for e in entries]


def get_paper_by_id(arxiv_id: str) -> dict | None:
    from .documents import ARXIV_ID
    if not ARXIV_ID.fullmatch(arxiv_id):
        raise ValueError("arXiv ID 格式不合法。")
    params = {"search_query": f"id:{arxiv_id}", "max_results": 1}
    resp = httpx.get(ARXIV_API, params=params, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    entry = root.find("atom:entry", NS)
    if entry is None:
        return None
    return _parse_entry(entry)
