"""FAQ lookup used to ground the AI (and as the offline fallback)."""
import re

from sqlalchemy import or_

from ..models import FAQ

_WORD = re.compile(r"[a-z0-9']+")
_STOPWORDS = {"the", "a", "an", "is", "i", "my", "to", "do", "how", "can", "what", "of",
              "for", "and", "it", "in", "on", "me", "you", "your", "with", "are", "why",
              "which", "when", "where", "who", "does", "be", "can't", "i'm", "this", "that",
              "there", "or", "if", "at", "so", "we", "our", "get", "want", "need", "please"}
# One keyword hit (2 points) or two words shared with the question (1 point each).
# A single shared question word is too weak: "why is the sky blue" isn't a sales FAQ.
_MIN_SCORE = 2


def _tokens(text):
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}


def articles_for(module):
    """FAQ query for one module; rows saved before modules existed count as "service"."""
    if module == "service":
        return FAQ.query.filter(or_(FAQ.module == "service", FAQ.module.is_(None)))
    return FAQ.query.filter_by(module=module)


def search(query, module="service", limit=3):
    """Return up to `limit` of the module's FAQs, ranked by keyword overlap with the query."""
    q = _tokens(query)
    if not q:
        return []
    scored = []
    for faq in articles_for(module).all():
        kw = {k.strip().lower() for k in faq.keywords.split(",") if k.strip()}
        score = 2 * len(q & kw) + len(q & _tokens(faq.question))
        if score >= _MIN_SCORE:
            scored.append((score, faq))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [faq for _, faq in scored[:limit]]


def as_context(faqs):
    if not faqs:
        return "No matching knowledge-base articles."
    return "\n\n".join(f"Q: {f.question}\nA: {f.answer}" for f in faqs)
