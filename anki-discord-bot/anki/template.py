"""
Minimal renderer for Anki's card templates (qfmt/afmt).

Supports:
  {{FieldName}}              -> field value
  {{#FieldName}}...{{/FieldName}}   -> shown only if field is non-empty
  {{^FieldName}}...{{/FieldName}}   -> shown only if field is empty
  {{FrontSide}}              -> rendered front (for afmt only)

Not supported (stripped/simplified): {{cloze:Field}} cloze deletions, and
Anki's more exotic template filters (e.g. {{furigana:Field}}). Good enough
for the majority of simple Basic/Basic-and-reversed style decks like Kaishi.
"""
import re

FIELD_RE = re.compile(r"\{\{([^#\^/}]+)\}\}")
COND_RE = re.compile(r"\{\{([#^])(.+?)\}\}(.*?)\{\{/\2\}\}", re.DOTALL)


def _resolve_conditionals(template: str, fields: dict[str, str]) -> str:
    def repl(match):
        kind, name, inner = match.group(1), match.group(2), match.group(3)
        value = fields.get(name, "")
        show = bool(value.strip()) if kind == "#" else not value.strip()
        return inner if show else ""

    # run twice to handle (rare) nesting one level deep
    for _ in range(2):
        template = COND_RE.sub(repl, template)
    return template


def render(template: str, fields: dict[str, str], front_side: str = "") -> str:
    template = template.replace("{{FrontSide}}", front_side)
    template = _resolve_conditionals(template, fields)

    def repl(match):
        name = match.group(1).strip()
        if name.startswith(("cloze:", "type:", "hint:", "furigana:", "kanji:", "kana:")):
            name = name.split(":", 1)[1]
        return fields.get(name, "")

    template = FIELD_RE.sub(repl, template)
    return template


def render_card(fields_list: list[str], field_names: list[str], qfmt: str, afmt: str) -> tuple[str, str]:
    """Returns (front_html, back_html) for a note given its template."""
    fields = dict(zip(field_names, fields_list))
    front = render(qfmt, fields)
    back = render(afmt, fields, front_side=front)
    return front, back
