"""Safe multipart email rendering independent of the delivery provider."""

import html
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class NotificationContent:
    """Structured strategy or SEC content supplied to notification adapters."""

    category: str
    title: str
    occurred_on: date
    cutoff_at: datetime
    status: str
    summary: str
    items: tuple[Mapping[str, Any], ...]
    source_links: tuple[str, ...]
    revision: str

    def __post_init__(self) -> None:
        if self.cutoff_at.tzinfo is None:
            raise ValueError("notification cutoff must be timezone-aware")
        if not all((self.category, self.title, self.status, self.revision)):
            raise ValueError("notification identity fields cannot be blank")


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """Plain-text and HTML alternatives plus deterministic delivery material."""

    subject: str
    plain_text: str
    html: str
    idempotency_material: str
    template_version: str


class EmailRenderer:
    """Render external values as text; never trust provider or filing markup."""

    def __init__(self, *, template_version: str) -> None:
        if not template_version:
            raise ValueError("template_version is required")
        self.template_version = template_version

    def render(self, content: NotificationContent) -> RenderedEmail:
        """Produce readable multipart alternatives without raw-data attachments."""
        plain_lines = [
            content.title,
            f"Category: {content.category}",
            f"Date: {content.occurred_on.isoformat()}",
            f"Cutoff: {content.cutoff_at.isoformat()}",
            f"Status: {content.status}",
            content.summary,
        ]
        html_parts = [
            f"<h1>{html.escape(content.title)}</h1>",
            "<dl>",
            f"<dt>Category</dt><dd>{html.escape(content.category)}</dd>",
            f"<dt>Date</dt><dd>{content.occurred_on.isoformat()}</dd>",
            f"<dt>Cutoff</dt><dd>{html.escape(content.cutoff_at.isoformat())}</dd>",
            f"<dt>Status</dt><dd>{html.escape(content.status)}</dd>",
            "</dl>",
            f"<p>{html.escape(content.summary)}</p>",
        ]
        if content.items:
            html_parts.append("<ul>")
        for item in content.items:
            rendered_pairs = [f"{key}: {value}" for key, value in sorted(item.items())]
            plain_lines.append("; ".join(rendered_pairs))
            html_parts.append(f"<li>{html.escape('; '.join(rendered_pairs), quote=True)}</li>")
        if content.items:
            html_parts.append("</ul>")
        for link in content.source_links:
            if _is_http_url(link):
                escaped = html.escape(link, quote=True)
                plain_lines.append(link)
                html_parts.append(f'<p><a href="{escaped}">{escaped}</a></p>')
        material = (
            f"{content.category}:{content.occurred_on.isoformat()}:{content.title}:"
            f"{content.revision}:{self.template_version}"
        )
        return RenderedEmail(
            subject=content.title.replace("\r", " ").replace("\n", " "),
            plain_text="\n".join(plain_lines),
            html="\n".join(html_parts),
            idempotency_material=material,
            template_version=self.template_version,
        )


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
