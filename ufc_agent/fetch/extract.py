"""Turn an HTML page into compact text an LLM can read.

Tables are kept as `cell | cell | cell` rows because stats pages are mostly tables.
With include_links, links render as `text <url>`, a table row's data-link as a trailing `<url>`,
and images as `[img:file.png]`, so the agent can follow event, fight and fighter pages.
"""
import re
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

DROP_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header", "head"]
BLOCK_TAGS = {
    "p", "div", "section", "article", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "br", "dt", "dd", "dl", "main", "aside", "blockquote", "figcaption",
}


def _cell_text(node):
    return " ".join(node.text(separator=" ").split())


def _table_to_text(table, include_links=False):
    lines = []
    # Captions carry table headers such as a rankings division's champion.
    caption = table.css_first("caption")
    if caption is not None and _cell_text(caption):
        lines.append(_cell_text(caption))
    for row in table.css("tr"):
        cells = [_cell_text(cell) for cell in row.css("th, td")]
        cells = [cell for cell in cells if cell]
        row_link = row.attributes.get("data-link") if include_links else None
        if row_link:
            cells.append(f"<{row_link}>")
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _inline_links(body, base_url):
    for img in body.css("img"):
        src = img.attributes.get("src") or ""
        name = urlparse(src).path.rsplit("/", 1)[-1]
        img.replace_with(f" [img:{name}] " if name else "")
    for link in body.css("a[href]"):
        href = (link.attributes.get("href") or "").strip()
        text = " ".join(link.text(separator=" ").split())
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urljoin(base_url, href) if base_url else href
        link.replace_with(f"{text} <{url}>" if text else f"<{url}>")


def html_to_text(html, max_chars=8000, include_links=False, base_url=None):
    """Return (title, text, truncated)."""
    tree = LexborHTMLParser(html)
    title_node = tree.css_first("title")
    title = " ".join(title_node.text().split()) if title_node else ""

    tree.strip_tags(DROP_TAGS)
    body = tree.body or tree.root
    if body is None:
        return title, "", False

    if include_links:
        _inline_links(body, base_url)

    # Replace each table with a text node holding its rows, innermost tables first.
    for table in reversed(body.css("table")):
        rendered = _table_to_text(table, include_links)
        table.replace_with(f"\n{rendered}\n" if rendered else "")

    for node in body.css(",".join(BLOCK_TAGS)):
        node.insert_after("\n")

    text = body.text(separator=" ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\n{3,}", "\n\n", text)

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rsplit("\n", 1)[0]
    return title, text, truncated
