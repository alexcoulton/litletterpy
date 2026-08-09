"""Render categorized papers as stable plain-text and HTML newsletters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from html import escape

from litletter.config import CategoryConfig, NewsletterConfig
from litletter.models import Paper
from litletter.storage import PendingPaper


@dataclass(frozen=True, slots=True)
class RenderedNewsletter:
    """Provider-independent newsletter content."""

    edition_id: str
    subject: str
    text: str
    html: str


def render_newsletter(
    config: NewsletterConfig,
    categories: tuple[CategoryConfig, ...],
    items: list[PendingPaper],
    *,
    edition_date: date,
) -> RenderedNewsletter:
    """Render one deterministic edition grouped by primary category."""
    category_names = {category.id: category.name for category in categories}
    grouped = {
        category.id: [item for item in items if item.primary_category_id == category.id]
        for category in categories
    }
    subject = _subject(config.title, len(items), edition_date)
    edition_id = _edition_id(edition_date, config.to, items)
    return RenderedNewsletter(
        edition_id=edition_id,
        subject=subject,
        text=_render_text(
            config,
            categories,
            grouped,
            category_names,
            edition_date,
        ),
        html=_render_html(
            config,
            categories,
            grouped,
            category_names,
            edition_date,
        ),
    )


def _subject(title: str, count: int, edition_date: date) -> str:
    noun = "paper" if count == 1 else "papers"
    return f"{title} — {count} new {noun} — {edition_date.isoformat()}"


def _edition_id(
    edition_date: date,
    recipients: tuple[str, ...],
    items: list[PendingPaper],
) -> str:
    components = [edition_date.isoformat(), *recipients]
    components.extend(
        f"{item.paper.source.value}:{item.paper.source_id}:"
        f"{','.join(item.category_ids)}"
        for item in items
    )
    digest = hashlib.sha256("\n".join(components).encode()).hexdigest()[:16]
    return f"{edition_date.isoformat()}-{digest}"


def _render_text(
    config: NewsletterConfig,
    categories: tuple[CategoryConfig, ...],
    grouped: dict[str, list[PendingPaper]],
    category_names: dict[str, str],
    edition_date: date,
) -> str:
    lines = [config.title, edition_date.isoformat(), ""]
    total = sum(len(values) for values in grouped.values())
    lines.append(f"{total} new {'paper' if total == 1 else 'papers'}")
    for category in categories:
        items = grouped[category.id]
        if not items:
            continue
        lines.extend(("", f"## {category.name} ({len(items)})", ""))
        for item in items:
            paper = item.paper
            lines.append(paper.title)
            lines.append(paper.url)
            lines.append(_metadata(paper))
            authors = _authors(paper)
            if authors:
                lines.append(authors)
            secondary = [
                category_names[category_id]
                for category_id in item.category_ids
                if category_id != item.primary_category_id
            ]
            if secondary:
                lines.append(f"Also in: {', '.join(secondary)}")
            abstract = _excerpt(paper.abstract, config.abstract_max_characters)
            if abstract:
                lines.extend(("", abstract))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_html(
    config: NewsletterConfig,
    categories: tuple[CategoryConfig, ...],
    grouped: dict[str, list[PendingPaper]],
    category_names: dict[str, str],
    edition_date: date,
) -> str:
    total = sum(len(values) for values in grouped.values())
    sections: list[str] = []
    for category in categories:
        items = grouped[category.id]
        if not items:
            continue
        cards = "".join(
            _paper_html(item, category_names, config.abstract_max_characters)
            for item in items
        )
        sections.append(
            '<section style="margin:32px 0">'
            f'<h2 style="font-size:22px;margin:0 0 16px">'
            f"{escape(category.name)} "
            f'<span style="color:#6b7280;font-weight:normal">({len(items)})</span>'
            "</h2>"
            f"{cards}</section>"
        )
    noun = "paper" if total == 1 else "papers"
    return (
        '<!doctype html><html><body style="margin:0;background:#f5f5f4;'
        'color:#1c1917;font-family:Arial,sans-serif">'
        '<main style="max-width:720px;margin:0 auto;padding:32px 20px">'
        '<header style="margin-bottom:28px">'
        f'<h1 style="font-size:30px;margin:0 0 8px">{escape(config.title)}</h1>'
        f'<div style="color:#57534e">{edition_date.isoformat()} · '
        f"{total} new {noun}</div></header>"
        f"{''.join(sections)}"
        '<footer style="color:#78716c;font-size:12px;margin-top:36px">'
        "Generated by Litletter.</footer></main></body></html>"
    )


def _paper_html(
    item: PendingPaper,
    category_names: dict[str, str],
    abstract_max_characters: int,
) -> str:
    paper = item.paper
    authors = _authors(paper)
    abstract = _excerpt(paper.abstract, abstract_max_characters)
    secondary = [
        category_names[category_id]
        for category_id in item.category_ids
        if category_id != item.primary_category_id
    ]
    details = [
        f'<div style="color:#57534e;font-size:14px">{escape(_metadata(paper))}</div>'
    ]
    if authors:
        details.append(
            f'<div style="color:#57534e;font-size:14px;margin-top:4px">'
            f"{escape(authors)}</div>"
        )
    if secondary:
        details.append(
            '<div style="color:#57534e;font-size:13px;margin-top:6px">'
            f"Also in: {escape(', '.join(secondary))}</div>"
        )
    if abstract:
        details.append(
            f'<p style="line-height:1.55;margin:14px 0 0">{escape(abstract)}</p>'
        )
    return (
        '<article style="background:#ffffff;border:1px solid #e7e5e4;'
        'border-radius:8px;margin:0 0 14px;padding:18px">'
        '<h3 style="font-size:18px;line-height:1.35;margin:0 0 9px">'
        f'<a href="{escape(paper.url, quote=True)}" '
        f'style="color:#1d4ed8;text-decoration:none">{escape(paper.title)}</a>'
        f"</h3>{''.join(details)}</article>"
    )


def _metadata(paper: Paper) -> str:
    parts = [paper.journal or paper.category or paper.source.value]
    if paper.published_at:
        parts.append(paper.published_at.isoformat())
    if paper.doi:
        parts.append(f"doi:{paper.doi}")
    else:
        parts.append(f"{paper.source.value}:{paper.source_id}")
    return " · ".join(parts)


def _authors(paper: Paper) -> str:
    names = [author.name for author in paper.authors]
    if len(names) > 8:
        return ", ".join(names[:8]) + ", et al."
    return ", ".join(names)


def _excerpt(value: str | None, maximum: int) -> str | None:
    if not value or maximum == 0:
        return None
    normalized = " ".join(value.split())
    if len(normalized) <= maximum:
        return normalized
    shortened = normalized[: max(1, maximum - 1)].rsplit(" ", 1)[0]
    return (shortened or normalized[: max(1, maximum - 1)]) + "…"
