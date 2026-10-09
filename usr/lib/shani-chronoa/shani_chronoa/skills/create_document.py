"""Skill: create a document - a markdown file, a plain-text file, or an HTML
page - from a title and body. The document-side of generate_image and of
write_text_file (which writes whatever text you give it; this one structures
it into a titled document).

The result is a new file; nothing is overwritten. Nothing leaves the machine.
"""


from shani_chronoa import files
from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_document",
        "description": (
            "Create a document from a title and body: a markdown file, a plain "
            "text file, or an HTML page. Writes a new file; never overwrites."
        ),
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "The document's title."},
            "body": {"type": "string", "description": "The main content."},
            "format": {"type": "string", "enum": ["md", "txt", "html"],
                        "description": "Output format (default md)."},
            "path": {"type": "string", "description": "Optional: where to save it."},
        }, "required": ["title", "body"]},
    },
}


def _run(arguments: dict) -> str:
    title = (arguments.get("title") or "").strip()
    body = (arguments.get("body") or "").strip()
    fmt = (arguments.get("format") or "md").lower().lstrip(".")
    if fmt not in ("md", "txt", "html"):
        return "format must be md, txt, or html."
    if not title:
        return "A document needs a title."
    if not body:
        return "A document needs a body."

    if arguments.get("path"):
        try:
            target = files.resolve(str(arguments["path"]).strip())
        except files.PathProblem as e:
            return str(e)
    else:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:40]
        target = files.data_home() / "shani-chronoa" / "documents" / f"{safe}.{fmt}"
    target.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    original = target
    while target.exists():
        target = original.with_name(f"{original.stem}-{n}{original.suffix}")
        n += 1

    if fmt == "md":
        content = f"# {title}\n\n{body}\n"
    elif fmt == "txt":
        content = f"{title}\n{'=' * len(title)}\n\n{body}\n"
    else:
        import html as _html
        content = (f"<!DOCTYPE html>\n<html>\n<head><meta charset='utf-8'>"
                   f"<title>{_html.escape(title)}</title>"
                   "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46em;margin:2em auto;padding:0 1em}"
                   "h1{font-size:1.8em}h2{font-size:1.3em;margin-top:1.4em}</style></head>\n<body>\n"
                   f"<h1>{_html.escape(title)}</h1>\n" + markdown_to_html(body) + "\n</body>\n</html>\n")
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Could not write {target}: {e}"
    return f"Saved {target} ({target.stat().st_size} bytes)."


POST_CONDITION = None

def markdown_to_html(body: str) -> str:
    """The small markdown a model writes - headings, lists, bold, paragraphs - as HTML.

    Every piece is escaped before any tag is added, so text from a web page or
    a model cannot become markup. Anything else stays literal text. The HTML
    format used to put the body in <p> blocks as-is, so an itinerary showed
    "## Flight - Boston..." as one run-on paragraph (seen in a recorded demo).
    """
    import html as _html
    import re as _re

    def inline(text: str) -> str:
        out = _html.escape(text)
        out = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
        return _re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<em>\1</em>", out)

    parts, para, items = [], [], []

    def flush():
        if para:
            parts.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if items:
            parts.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in body.splitlines():
        stripped = line.strip()
        heading = _re.match(r"^(#{1,6})\s+(.*)$", stripped)
        bullet = _re.match(r"^[-*+]\s+(.*)$", stripped)
        if not stripped:
            flush()
        elif heading:
            flush()
            level = max(2, len(heading.group(1)))  # the title is the only <h1>
            parts.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif bullet:
            if para:
                flush()
            items.append(bullet.group(1))
        else:
            if items:
                flush()
            para.append(stripped)
    flush()
    return "\n".join(parts)


SKILLS = [Skill(name="create_document", schema=_SCHEMA, run=_run)]
