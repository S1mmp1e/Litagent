import os

import click
from rich.console import Console
from rich.table import Table

from .database import init_db
from .search import search_papers as arxiv_search
from .tools import (
    add_to_library,
    export_bibtex,
    get_paper_detail,
    list_library,
    search_library,
)

console = Console()


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """LitAgent — Literature Management Agent.

    Search, store, organize, and summarize academic papers with AI assistance.
    """
    init_db()


@cli.command()
@click.argument("query")
@click.option("--max-results", "-n", default=10, help="Max search results")
def search(query, max_results):
    """Search arXiv for papers."""
    papers = arxiv_search(query, max_results)
    if not papers:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(title=f"arXiv search: [bold]{query}[/bold]")
    table.add_column("#", style="cyan", width=4)
    table.add_column("arXiv ID", style="green", width=14)
    table.add_column("Title", width=60)
    table.add_column("Year", width=6)
    table.add_column("Authors", width=30)

    for i, p in enumerate(papers, 1):
        authors = ", ".join(p["authors"][:2])
        if len(p["authors"]) > 2:
            authors += " et al."
        table.add_row(
            str(i),
            p["arxiv_id"],
            p["title"][:80],
            str(p["year"]),
            authors,
        )

    console.print(table)


@cli.command()
@click.argument("arxiv_id")
def add(arxiv_id):
    """Add a paper to library by arXiv ID."""
    result = add_to_library(arxiv_id)
    console.print(f"[green]{result}[/green]")


@cli.command()
@click.option("--tag", "-t", default=None, help="Filter by tag")
@click.option("--limit", "-n", default=20, help="Max entries")
def list(tag, limit):
    """List papers in library."""
    console.print(list_library(tag, limit))


@cli.command()
@click.argument("paper_id", type=int)
def detail(paper_id):
    """Show full details of a paper."""
    result = get_paper_detail(paper_id)
    if result.startswith("{"):
        console.print_json(result)
    else:
        console.print(result)


@cli.command("search-lib")
@click.argument("query")
def search_lib(query):
    """Search the local library."""
    console.print(search_library(query))


@cli.command()
@click.argument("paper_ids")
def export(paper_ids):
    """Export papers as BibTeX (comma-separated IDs)."""
    console.print(export_bibtex(paper_ids))


@cli.command()
@click.argument("message", required=False)
def chat(message):
    """Interactive AI chat using server-side DeepSeek configuration."""
    from .agent_openai import run_agent_openai

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LIT_API_KEY")
    if not api_key:
        console.print("[red]Error: DEEPSEEK_API_KEY is not configured.[/red]")
        return

    if message:
        console.print(f"[bold]You:[/bold] {message}")
        with console.status("[green]Agent is thinking...[/green]"):
            response, _ = run_agent_openai(api_key, message)
        console.print(f"\n[bold]Agent:[/bold]\n{response}")
    else:
        console.print(
            "[bold]LitAgent Chat Mode[/bold] — Type 'quit' to exit.\n"
        )
        messages = []
        while True:
            try:
                msg = console.input("[bold green]You:[/bold green] ")
            except (EOFError, KeyboardInterrupt):
                console.print("\nGoodbye!")
                break
            if msg.lower() in ("quit", "exit", "q"):
                console.print("Goodbye!")
                break
            if not msg.strip():
                continue
            with console.status("[green]Agent is thinking...[/green]"):
                response, messages = run_agent_openai(api_key, msg, messages)
            console.print(f"\n[bold]Agent:[/bold]\n{response}\n")


if __name__ == "__main__":
    cli()
