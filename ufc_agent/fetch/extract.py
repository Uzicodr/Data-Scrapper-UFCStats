"""Turn an HTML page into compact text an LLM can read.

Tables are kept as `cell | cell | cell` rows because stats pages are mostly tables.
"""
import re

from selectolax.lexbor import LexborHTMLParser

DROP_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header", "head"]
BLOCK_TAGS = {
    "p", "div", "section", "article", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "br", "dt", "dd", "dl", "main", "aside", "blockquote", "figcaption",
}


def _cell_text(node):
    return " ".join(node.text(separator=" ").split())


def _table_to_text(table):
    lines = []
    for row in table.css("tr"):
        cells = [_cell_text(cell) for cell in row.css("th, td")]
        cells = [cell for cell in cells if cell]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def html_to_text(html, max_chars=8000):
    """Return (title, text, truncated)."""
    tree = LexborHTMLParser(html)
    title_node = tree.css_first("title")
    title = " ".join(title_node.text().split()) if title_node else ""

    tree.strip_tags(DROP_TAGS)
    body = tree.body or tree.root
    if body is None:
        return title, "", False

    # Replace each table with a text node holding its rows, innermost tables first.
    for table in reversed(body.css("table")):
        rendered = _table_to_text(table)
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
