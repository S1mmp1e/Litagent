import json


def to_bibtex(papers) -> str:
    """Convert a list of Paper ORM objects to BibTeX string."""
    entries = []
    for p in papers:
        authors = json.loads(p.authors)

        if authors:
            first_author = authors[0].split()[-1] if authors[0] else "unknown"
        else:
            first_author = "unknown"

        key = f"{first_author}{p.year}{p.title.split()[0].lower() if p.title else 'paper'}"

        author_str = " and ".join(authors)
        entry_type = "article"

        lines = [f"@{entry_type}{{{key},"]
        lines.append(f"  title = {{{p.title}}},")
        lines.append(f"  author = {{{author_str}}},")
        lines.append(f"  year = {{{p.year}}},")
        if p.doi:
            lines.append(f"  doi = {{{p.doi}}},")
        if p.arxiv_id:
            lines.append(f"  eprint = {{{p.arxiv_id}}},")
            lines.append(f"  archiveprefix = {{arXiv}},")
        if p.venue:
            lines.append(f"  journal = {{{p.venue}}},")
        lines.append(f"  abstract = {{{p.abstract}}},")
        lines.append("}")

        entries.append("\n".join(lines))

    return "\n\n".join(entries)
