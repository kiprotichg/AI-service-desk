"""AI reply generation.

Three assistants (modes): the ERP Service module, the ERP Sales module, and a
general assistant. The live model is either Claude or Gemini (AI_PROVIDER). Both are asked
for the same JSON reply, so every answer also carries intent, sentiment and an
escalation decision. When no key is configured, or the live call fails for any
reason, a keyword responder answers instead so the chat keeps working.
"""
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

from flask import current_app

from . import knowledge_base

log = logging.getLogger(__name__)

# "question" = a message outside the current mode's topic (science, coding, advice...).
# Service-module intents, then Sales-module intents.
SERVICE_INTENTS = ["service_request", "scheduling", "job_card", "spare_parts", "warranty",
                   "contract", "maintenance", "service_billing", "service_report"]
SALES_INTENTS = ["quotation", "sales_order", "invoicing", "pricing", "customer",
                 "sales_report"]
INTENTS = ["question", "general", *SERVICE_INTENTS, *SALES_INTENTS]
SENTIMENTS = ["positive", "neutral", "negative"]
MAX_OUTPUT_TOKENS = 4000  # room for longer explanations, not just short support replies

PRODUCT_NAME = os.getenv("PRODUCT_NAME", "Ideal Dynamics")

# Three answer modes, picked per message with the switch at the top of the chat.
MODES = ("service", "sales", "general")

SWITCH_HINT = ("switch modes at the top of the chat: Service for the Service module, "
               "Sales for the Sales module, and General for anything else")

SERVICE_PROMPT = f"""You are the Service module assistant for {PRODUCT_NAME}, an
integrated ERP suite. You help staff use the Service module: logging and tracking
service requests (service calls), assigning and scheduling technicians, job cards
and work orders, spare parts used on jobs, equipment and serial numbers, warranties,
service contracts and SLA response times, preventive maintenance schedules, billing
for service work, and service reports.

Answer from the Service knowledge-base articles sent with each message. If the
articles don't cover the question, you may explain the general service-management
idea (for example how a service request becomes a scheduled job and then an
invoice), but say clearly that the exact screens and steps in their {PRODUCT_NAME}
setup may differ, and offer to pass the question to the support team. Never invent
menu names, field names, settings or features. Use attached screenshots, photos or
files (an error message, a job card, a photo of faulty equipment) to understand the
problem.

Only handle Service module matters. If the message is about something else, don't
answer it. Say briefly that this is Service mode, and that they can {SWITCH_HINT}.
Use intent "question" only for these off-topic messages; a general question about
service requests, scheduling, job cards, parts, warranties, contracts, maintenance,
billing or reports still gets that topic.

Be practical and clear. Use short numbered steps for how-to answers. Write plain
text without markdown symbols (no **, #, or tables), because the chat window shows
text as-is.

Set "escalate" to true when the user asks for a person, reports an error or bug,
reports wrong data (a job card or contract that won't save, wrong SLA dates, parts
stock or charges that look wrong), has a permission or access problem, or asks
something specific to their system that the articles don't answer. Use one of these
intents: {", ".join(SERVICE_INTENTS)}, general or question. Leave "ticket_subject"
empty unless you escalate.

Service tickets need two details: the equipment serial number and the customer site
(the customer's location or branch where the equipment is). Fill "serial_number" and
"customer_site" whenever the user has given them anywhere in the conversation, copied
exactly as written; otherwise leave them empty. Never guess or make one up. When you
escalate, or a ticket is already open, and either detail is still unknown, end your
reply by asking for the missing one(s)."""

SALES_PROMPT = f"""You are the Sales module assistant for {PRODUCT_NAME}, an
integrated ERP suite. You help staff use the Sales module: customers, quotations,
sales orders, deliveries, sales invoices, credit notes and returns, price lists and
discounts, credit limits, and sales reports.

Answer from the Sales knowledge-base articles sent with each message. If the
articles don't cover the question, you may explain the general sales-process idea
(for example how a quotation becomes a sales order and then an invoice), but say
clearly that the exact screens and steps in their {PRODUCT_NAME} setup may differ, and
offer to pass the question to the support team. Never invent menu names, field
names, settings or features. Use attached screenshots, documents or files (an error
message, a quotation, an invoice) to understand the problem.

Only handle Sales module matters. If the message is about something else, don't
answer it. Say briefly that this is Sales mode, and that they can {SWITCH_HINT}.
Use intent "question" only for these off-topic messages; a general question about
quotations, orders, invoicing, pricing, customers or reports still gets that topic.

Be practical and clear. Use short numbered steps for how-to answers. Write plain
text without markdown symbols (no **, #, or tables), because the chat window shows
text as-is.

Set "escalate" to true when the user asks for a person, reports an error or bug,
reports wrong data (totals, taxes, stock or balances that look wrong, a document that
won't post), has a permission or access problem, or asks something specific to their
system that the articles don't answer. Use one of these intents:
{", ".join(SALES_INTENTS)}, general or question. Leave "ticket_subject" empty unless
you escalate. Always set "serial_number" and "customer_site" to empty strings."""

GENERAL_PROMPT = f"""You are a friendly, knowledgeable general assistant. People can
ask you anything: science, history, maths, coding, writing, health basics, advice,
definitions, recommendations or small talk. Answer fully and accurately from your
own knowledge, the way a helpful expert would. Match the length to the question: a
sentence or two for simple facts, a few short paragraphs or numbered steps for
explanations. If you're unsure, or the facts may have changed recently, say so. For
medical, legal or financial questions, give helpful general information and suggest
a professional for their situation. Read any attached photos or files carefully and
use them in your answer.

You can't see anyone's {PRODUCT_NAME} data. If someone needs help using the
{PRODUCT_NAME} Service module (service requests, technicians, contracts, warranties)
or Sales module (quotations, orders, invoices), tell them they can {SWITCH_HINT}.

Write plain text without markdown symbols (no **, #, or tables), because the chat
window shows text as-is. Plain numbered lists and line breaks are fine.

Always set "escalate" to false, and "ticket_subject", "serial_number" and
"customer_site" to empty strings. Use intent
"question", or the closest topic if they ask about the Service or Sales module. Judge
"sentiment" from the tone of the message."""

MAX_SUGGESTIONS = 3

SUGGESTIONS_RULE = """

After every reply, fill "suggestions" with 3 short follow-up questions the user is
likely to ask next, written in the user's own voice (for example "How do I add spare
parts to this job?"). Each one is a single question of at most 70 characters, about
what you just discussed, and something you could answer well in this mode. Don't
repeat the user's message or anything already answered, and don't number them."""

SERVICE_PROMPT += SUGGESTIONS_RULE
SALES_PROMPT += SUGGESTIONS_RULE
GENERAL_PROMPT += SUGGESTIONS_RULE

PROMPTS = {"service": SERVICE_PROMPT, "sales": SALES_PROMPT, "general": GENERAL_PROMPT}

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "intent": {"type": "string", "enum": INTENTS},
        "sentiment": {"type": "string", "enum": SENTIMENTS},
        "escalate": {"type": "boolean"},
        "ticket_subject": {"type": "string"},
        "serial_number": {"type": "string"},   # Service tickets only, "" if unknown
        "customer_site": {"type": "string"},   # Service tickets only, "" if unknown
        "suggestions": {"type": "array", "items": {"type": "string"}},  # follow-up questions
    },
    "required": ["reply", "intent", "sentiment", "escalate", "ticket_subject",
                 "serial_number", "customer_site", "suggestions"],
    "additionalProperties": False,
}

DECLINED_REPLY = "I'm not able to help with that here. Let me connect you with a human agent."


@dataclass
class AIReply:
    reply: str
    intent: str = "general"
    sentiment: str = "neutral"
    escalate: bool = False
    ticket_subject: str = ""
    serial_number: str = ""   # equipment serial number (Service mode)
    customer_site: str = ""   # customer location/branch (Service mode)
    suggestions: list = field(default_factory=list)  # follow-up questions to offer
    sources: list = field(default_factory=list)      # [{"title", "url"}] from web search
    notice: str = ""                                  # e.g. web search couldn't be used
    source: str = "offline"  # "claude" | "gemini" | "offline"


def generate_reply(history, user_message, attachments=(), mode="service", web_search=False):
    """Reply to user_message (plus any uploaded files).

    history: [{"role", "content", "attachments": [FilePart]}] oldest first,
    excluding the current message. attachments: FileParts sent with this message.
    mode: "service" (store help, FAQ-grounded, can open tickets) or "general"
    (answers anything, never opens tickets).
    web_search: let the model search the web (Gemini: Google Search grounding;
    Claude: the web search tool). If that call fails, it's retried without search.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    attachments = list(attachments)
    # Service and Sales each search their own articles; General uses none.
    faqs = (knowledge_base.search(user_message, module=mode)
            if user_message and mode != "general" else [])
    cfg = current_app.config
    reply = None
    if cfg["AI_ENABLED"]:
        provider = cfg["AI_PROVIDER"]
        global _search_off_until
        try_search = web_search and time.monotonic() >= _search_off_until
        for search in ([True, False] if try_search else [False]):
            try:
                reply = _PROVIDERS[provider](
                    history[-cfg["MAX_HISTORY_MESSAGES"]:],
                    _user_turn(user_message, faqs, attachments, mode, search),
                    attachments, PROMPTS[mode], web_search=search)
                if web_search and not search:
                    reply.notice = WEB_SEARCH_UNAVAILABLE
                break
            except Exception as exc:
                if search and _is_quota_error(exc):
                    # Search quota used up: skip search for a while instead of waiting
                    # for the retries on every message.
                    _search_off_until = time.monotonic() + SEARCH_COOLDOWN_SECONDS
                # Deliberately broad: network errors, rate limits, bad keys and malformed
                # output differ per SDK, and every one of them should degrade (to an
                # answer without search, then the offline answer) instead of failing.
                log.exception("%s call failed (web search %s)", provider,
                              "on" if search else "off")
    if reply is None:
        reply = _offline_reply(user_message, faqs, has_files=bool(attachments), mode=mode)
        if web_search:
            reply.notice = WEB_SEARCH_UNAVAILABLE
    return _finish(reply, user_message, mode)


def _finish(reply, user_message, mode):
    """Rules every reply gets, whichever model (or the offline responder) wrote it."""
    if mode == "general":
        # General mode never opens tickets, whatever the model returned.
        reply.escalate = False
        reply.ticket_subject = ""
    if mode != "service":
        # Serial number and site belong to Service tickets only.
        reply.serial_number = reply.customer_site = ""
    elif not (reply.serial_number and reply.customer_site):
        # Backstop for offline mode and for anything the model missed.
        serial, site = extract_service_details(user_message)
        reply.serial_number = reply.serial_number or serial
        reply.customer_site = reply.customer_site or site
    reply.suggestions = clean_suggestions(reply.suggestions, user_message)
    if not reply.suggestions:
        # The model left them out, or we're offline: offer the topic's usual next steps.
        reply.suggestions = clean_suggestions(_offline_suggestions(reply.intent, mode),
                                              user_message)
    return reply


def clean_suggestions(items, user_message=""):
    """Up to MAX_SUGGESTIONS short, distinct questions, none repeating the user's message."""
    if not isinstance(items, (list, tuple)):
        return []
    asked = _norm(user_message)
    out, seen = [], set()
    for item in items:
        text = re.sub(r"^\s*(?:\d+[.)]|[-*\u2022])\s*", "", str(item or "")).strip()
        text = " ".join(text.split())[:100]
        key = _norm(text)
        if len(key) < 3 or key in seen or key == asked:
            continue
        seen.add(key)
        out.append(text)
        if len(out) == MAX_SUGGESTIONS:
            break
    return out


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


STREAM_FORMAT = """

Reply format: write your answer for the user first, as plain text. After it, on a new
line, write <meta>, then one line of JSON with the keys "intent", "sentiment",
"escalate", "ticket_subject", "serial_number", "customer_site" and "suggestions"
(following the rules above), then </meta>. Nothing comes after </meta>, and the answer
never mentions this block. Example ending:
<meta>{"intent": "general", "sentiment": "neutral", "escalate": false, "ticket_subject": "", "serial_number": "", "customer_site": "", "suggestions": ["...", "...", "..."]}</meta>"""


def stream_reply(history, user_message, attachments=(), mode="service", web_search=False):
    """Like generate_reply, but yields the answer while it's being written.

    Yields ("delta", text) pieces, possibly ("reset", None) if a half-written answer
    had to be thrown away, and finally ("done", AIReply). Gemini streams for real;
    Claude and the offline responder deliver the whole answer as one piece.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    cfg = current_app.config
    if not (cfg["AI_ENABLED"] and cfg["AI_PROVIDER"] == "gemini"):
        reply = generate_reply(history, user_message, attachments, mode, web_search)
        yield ("delta", reply.reply)
        yield ("done", reply)
        return

    global _search_off_until
    attachments = list(attachments)
    faqs = (knowledge_base.search(user_message, module=mode)
            if user_message and mode != "general" else [])
    history = history[-cfg["MAX_HISTORY_MESSAGES"]:]
    try_search = web_search and time.monotonic() >= _search_off_until
    for search in ([True, False] if try_search else [False]):
        started = False
        try:
            turn = _user_turn(user_message, faqs, attachments, mode, search)
            for kind, value in _gemini_stream(history, turn, attachments,
                                              PROMPTS[mode] + STREAM_FORMAT, search,
                                              mode, user_message):
                if kind == "delta":
                    started = True
                    yield ("delta", value)
                else:
                    reply = value
            if web_search and not search:
                reply.notice = WEB_SEARCH_UNAVAILABLE
            yield ("done", _finish(reply, user_message, mode))
            return
        except Exception as exc:
            if search and _is_quota_error(exc):
                _search_off_until = time.monotonic() + SEARCH_COOLDOWN_SECONDS
            log.exception("gemini stream failed (web search %s)", "on" if search else "off")
            if started:
                yield ("reset", None)
    reply = _offline_reply(user_message, faqs, has_files=bool(attachments), mode=mode)
    if web_search:
        reply.notice = WEB_SEARCH_UNAVAILABLE
    reply = _finish(reply, user_message, mode)
    yield ("delta", reply.reply)
    yield ("done", reply)


class _MetaSplitter:
    """Splits streamed text into the visible answer and the trailing <meta> block.

    Holds back a few characters that might be the start of "<meta>", so the tag never
    flashes on screen when it arrives split across chunks.
    """
    TAG = "<meta>"

    def __init__(self):
        self.pending, self.visible, self.meta_raw, self.in_meta = "", "", "", False

    def feed(self, text):
        if self.in_meta:
            self.meta_raw += text
            return ""
        self.pending += text
        at = self.pending.find(self.TAG)
        if at != -1:
            out, self.meta_raw = self.pending[:at], self.pending[at + len(self.TAG):]
            self.pending, self.in_meta = "", True
        else:
            keep = next((k for k in range(min(len(self.TAG) - 1, len(self.pending)), 0, -1)
                         if self.TAG.startswith(self.pending[-k:])), 0)
            cut = len(self.pending) - keep
            out, self.pending = self.pending[:cut], self.pending[cut:]
        self.visible += out
        return out

    def close(self):
        out = "" if self.in_meta else self.pending
        self.pending = ""
        self.visible += out
        return out

    def meta(self):
        raw = self.meta_raw.split("</meta>")[0]
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end < start:
            return None
        try:
            data = json.loads(raw[start:end + 1])
        except ValueError:
            return None
        return data if isinstance(data, dict) else None


def _reply_from_stream(text, meta, mode, user_message, source):
    """An AIReply from streamed text and its <meta> block (guessing if the block is missing)."""
    text = text.strip()
    if not text:
        raise ValueError("model returned an empty reply")
    if meta is None:
        log.warning("%s reply had no usable <meta> block; using keyword fallbacks", source)
        topic = _detect_intent(user_message, mode) if mode in _INTENT_PATTERNS else "question"
        wants_human = mode != "general" and bool(_HUMAN.search(user_message or ""))
        meta = {"intent": topic, "escalate": wants_human,
                "ticket_subject": (user_message or "")[:80] if wants_human else "",
                "sentiment": ("negative" if _NEGATIVE.search(user_message or "")
                              else "positive" if _POSITIVE.search(user_message or "") else "neutral")}
    return _parse_reply(json.dumps({**meta, "reply": text}), source)


def _is_overloaded(exc):
    """Errors where another model is worth trying: busy, rate limited, unavailable."""
    text = str(exc).lower()
    return any(s in text for s in ("503", "unavailable", "high demand", "overloaded",
                                   "429", "resource_exhausted", "500", "internal", "404",
                                   "not found"))


def _gemini_models():
    cfg = current_app.config
    models = [cfg["GEMINI_MODEL"]]
    fallback = cfg.get("GEMINI_FALLBACK_MODEL")
    if fallback and fallback not in models:
        models.append(fallback)
    return models


def _gemini_config(system, web_search, json_output=False):
    from google.genai import types
    extra = {}
    level = current_app.config.get("GEMINI_THINKING")
    if level:
        extra["thinking_config"] = types.ThinkingConfig(thinking_level=level)
    if json_output:
        extra.update(response_mime_type="application/json", response_json_schema=REPLY_SCHEMA)
    return types.GenerateContentConfig(
        system_instruction=system, max_output_tokens=MAX_OUTPUT_TOKENS,
        tools=[types.Tool(google_search=types.GoogleSearch())] if web_search else None, **extra)


def _gemini_stream(history, user_turn, attachments, system, web_search, mode, user_message):
    """Yield ("delta", text)... then ("reply", AIReply). Tries the fallback model if the
    main one is overloaded before it has written anything."""
    contents = _gemini_contents(history, user_turn, attachments)
    config = _gemini_config(system, web_search)
    models = _gemini_models()
    for i, model in enumerate(models):
        splitter, sources, started = _MetaSplitter(), [], False
        try:
            for chunk in _get_gemini_client().models.generate_content_stream(
                    model=model, contents=contents, config=config):
                piece = splitter.feed(chunk.text or "")
                if piece:
                    started = True
                    yield ("delta", piece)
                if web_search:
                    sources = _gemini_sources(chunk) or sources
        except Exception as exc:
            if (started or i == len(models) - 1 or not _is_overloaded(exc)
                    or (web_search and _is_quota_error(exc))):   # search quota covers every model
                raise
            log.warning("%s unavailable (%s); trying %s", model, str(exc)[:80], models[i + 1])
            continue
        tail = splitter.close()
        if tail:
            yield ("delta", tail)
        if not splitter.visible.strip():
            yield ("reply", _declined("gemini"))   # blocked, or stopped without output
            return
        reply = _reply_from_stream(splitter.visible, splitter.meta(), mode, user_message, "gemini")
        reply.sources = sources
        yield ("reply", reply)
        return


# Explicit forms only ("serial no: AB-1234", "S/N 99812", "site: Nairobi branch");
# the live model handles everyday phrasing. A serial must contain a digit.
_SERIAL = re.compile(r"\b(?:serial(?:\s*(?:no\.?|number|num|#))?|s/n|sn)\s*(?:is|:|#|-|=)?\s*"
                     r"([A-Za-z0-9][A-Za-z0-9\-/.]{2,39})", re.I)
_SITE = re.compile(r"\b(?:customer\s+)?site\s*(?::|=|\bis\b)\s*([^\n.;!?]{2,120})", re.I)


def extract_service_details(text):
    """Return (serial_number, customer_site) found in text, "" for each one missing."""
    serial = site = ""
    m = _SERIAL.search(text or "")
    if m and any(ch.isdigit() for ch in m.group(1)):
        serial = m.group(1).rstrip(".-/")
    m = _SITE.search(text or "")
    if m:
        site = m.group(1).strip(" ,")[:200]
    return serial, site


SEARCH_COOLDOWN_SECONDS = 15 * 60
_search_off_until = 0.0   # time.monotonic() until which web search is skipped


def _is_quota_error(exc):
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "quota" in text or "rate limit" in text


WEB_SEARCH_UNAVAILABLE = ("Web search wasn't available for this answer, so it comes from the "
                          "AI's own knowledge and may be out of date.")

WEB_SEARCH_NOTE = ("<web_search>Web search is on. Search the web when the question needs "
                   "current, local or specific facts, and base the answer on what you "
                   "find.</web_search>\n\n")


def _user_turn(user_message, faqs, attachments=(), mode="service", web_search=False):
    # Knowledge-base context goes in the user turn (not the system prompt) so the
    # system prompt stays identical across requests and can be cached.
    text = user_message or "(The user sent the attached file(s) without a message.)"
    files = ""
    if attachments:
        names = ", ".join(f"{a.filename} ({a.mime_type})" for a in attachments)
        files = f"<attached_files>{names}</attached_files>\n\n"
    search = WEB_SEARCH_NOTE if web_search else ""
    if mode == "general":
        return f"{search}{files}<user_message>\n{text}\n</user_message>"
    return (f"<knowledge_base>\n{knowledge_base.as_context(faqs)}\n</knowledge_base>\n\n"
            f"{search}{files}<user_message>\n{text}\n</user_message>")


def _file_as_text(part):
    """Text files are sent as text; files left out to save space become a short note."""
    if part.data is None:
        return f"[{part.filename} was attached earlier; it isn't included again to save space]"
    return f'<file name="{part.filename}">\n{part.data.decode("utf-8")}\n</file>'


def _parse_reply(text, source):
    """Turn the model's JSON into an AIReply, guarding against off-schema values."""
    data = json.loads(text)
    reply = str(data.get("reply") or "").strip()
    if not reply:
        raise ValueError("model returned an empty reply")
    intent = data.get("intent")
    sentiment = data.get("sentiment")
    return AIReply(
        reply=reply,
        intent=intent if intent in INTENTS else "general",
        sentiment=sentiment if sentiment in SENTIMENTS else "neutral",
        escalate=bool(data.get("escalate")),
        ticket_subject=str(data.get("ticket_subject") or "")[:200],
        serial_number=str(data.get("serial_number") or "").strip()[:100],
        customer_site=str(data.get("customer_site") or "").strip()[:200],
        suggestions=data.get("suggestions") or [],
        source=source,
    )


def _declined(source):
    return AIReply(reply=DECLINED_REPLY, escalate=True,
                   ticket_subject="Request needs human review", source=source)


# --------------------------------------------------------------------- Claude
_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    return _anthropic_client


def _claude_content(text, parts):
    """Message content for Claude: files first (images, PDFs, text), then the text."""
    if not parts:
        return text
    blocks = []
    for p in parts:
        if p.data is None or p.is_text:
            blocks.append({"type": "text", "text": _file_as_text(p)})
        else:
            source = {"type": "base64", "media_type": p.mime_type,
                      "data": base64.standard_b64encode(p.data).decode("ascii")}
            kind = "document" if p.mime_type == "application/pdf" else "image"
            blocks.append({"type": kind, "source": source})
    if text:
        blocks.append({"type": "text", "text": text})
    return blocks


CLAUDE_WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}


def _claude_reply(history, user_turn, attachments=(), system=SERVICE_PROMPT, web_search=False):
    messages = [{"role": m["role"], "content": _claude_content(m["content"], m.get("attachments"))}
                for m in history]
    messages.append({"role": "user", "content": _claude_content(user_turn, attachments)})
    extra = {"tools": [CLAUDE_WEB_SEARCH]} if web_search else {}
    response = _get_anthropic_client().beta.messages.create(
        **extra,
        model=current_app.config["CLAUDE_MODEL"],
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=messages,
        output_config={
            "effort": "low",  # short chat replies; raise if quality needs it
            "format": {"type": "json_schema", "schema": REPLY_SCHEMA},
        },
        # On a safety decline, let the API retry on a fallback model automatically.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return _declined("claude")
    # With web search the content mixes search calls, results and text; the JSON
    # answer is the text after the last search.
    text, sources = "", []
    for block in response.content:
        if block.type == "text":
            text += block.text
        else:
            text = ""
            if block.type == "web_search_tool_result" and isinstance(block.content, list):
                sources += [{"title": getattr(r, "title", "") or r.url, "url": r.url}
                            for r in block.content if getattr(r, "url", None)]
    reply = _parse_reply(text, "claude")
    reply.sources = _unique_sources(sources)
    return reply


def _unique_sources(items, limit=6):
    out, seen = [], set()
    for s in items:
        url = str(s.get("url") or "")
        if url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            out.append({"title": str(s.get("title") or url)[:200], "url": url[:2000]})
        if len(out) == limit:
            break
    return out


# --------------------------------------------------------------------- Gemini
_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        from google.genai import types
        # Gemini often answers 503 "high demand" for a moment; retry those (and rate
        # limits / other temporary server errors) briefly, then the fallback model
        # (GEMINI_FALLBACK_MODEL) takes over. Worst case adds about 0.5 + 1 seconds.
        retry = types.HttpRetryOptions(attempts=3, initial_delay=0.5, max_delay=2.0,
                                       exp_base=2.0, http_status_codes=[429, 500, 502, 503, 504])
        _gemini_client = genai.Client(  # reads GEMINI_API_KEY (or GOOGLE_API_KEY)
            http_options=types.HttpOptions(retry_options=retry))
    return _gemini_client


def _gemini_parts(text, parts):
    from google.genai import types
    out = []
    for p in parts or ():
        if p.data is None or p.is_text:
            out.append(types.Part.from_text(text=_file_as_text(p)))
        else:
            out.append(types.Part.from_bytes(data=p.data, mime_type=p.mime_type))
    if text:
        out.append(types.Part.from_text(text=text))
    return out


def _gemini_contents(history, user_turn, attachments=()):
    from google.genai import types
    # Gemini calls the assistant role "model".
    contents = [
        types.Content(role="model" if m["role"] == "assistant" else "user",
                      parts=_gemini_parts(m["content"], m.get("attachments")))
        for m in history
    ]
    contents.append(types.Content(role="user", parts=_gemini_parts(user_turn, attachments)))
    return contents


def _gemini_reply(history, user_turn, attachments=(), system=SERVICE_PROMPT, web_search=False):
    contents = _gemini_contents(history, user_turn, attachments)
    config = _gemini_config(system, web_search, json_output=True)
    models = _gemini_models()
    for i, model in enumerate(models):
        try:
            response = _get_gemini_client().models.generate_content(
                model=model, contents=contents, config=config)
            break
        except Exception as exc:
            if (i == len(models) - 1 or not _is_overloaded(exc)
                    or (web_search and _is_quota_error(exc))):   # search quota covers every model
                raise
            log.warning("%s unavailable (%s); trying %s", model, str(exc)[:80], models[i + 1])
    # A safety block (or any other stop without output) comes back with no text.
    if not response.text:
        return _declined("gemini")
    reply = _parse_reply(response.text, "gemini")
    if web_search:
        reply.sources = _gemini_sources(response)
    return reply


def _gemini_sources(response):
    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
    except (AttributeError, IndexError, TypeError):
        return []
    return _unique_sources([{"title": c.web.title, "url": c.web.uri}
                            for c in chunks if getattr(c, "web", None)])


_PROVIDERS = {"claude": _claude_reply, "gemini": _gemini_reply}


# ---------------------------------------------------------------- offline mode
_HUMAN = re.compile(r"\b(human|agent|person|representative|manager)\b", re.I)
_NEGATIVE = re.compile(r"\b(angry|terrible|awful|worst|broken|useless|furious|unacceptable|"
                       r"damaged|never arrived|scam)\b", re.I)
_POSITIVE = re.compile(r"\b(thanks|thank you|great|awesome|perfect|love)\b", re.I)
# Whole-word patterns, so "happy" doesn't match "app" and "border" doesn't match "order".
def _patterns(groups):
    return {intent: re.compile(r"\b(?:" + "|".join(words) + r")\b", re.I)
            for intent, words in groups.items()}


_INTENT_PATTERNS = {
    # Checked in order, most specific first: "SLA compliance report" is a report,
    # "assign a technician to this job" is scheduling.
    "service": _patterns({
        "service_report": [r"reports?", r"analysis", r"dashboards?", r"export"],
        "warranty": [r"warrant(y|ies)", r"guarantees?"],
        "contract": [r"contracts?", r"slas?", r"amcs?", r"renewals?", r"expir(y|ed|es|ing)"],
        "maintenance": [r"preventive", r"preventative", r"maintenance", r"ppm"],
        "spare_parts": [r"spare parts?", r"spares?", r"parts?", r"stock"],
        "service_billing": [r"invoic(e|es|ed|ing)", r"bill(s|ing|able)?", r"charge[sd]?"],
        "scheduling": [r"schedul(e|es|ed|ing)", r"technicians?", r"engineers?", r"visits?",
                       r"assign(ed|ing)?", r"dispatch(ed)?"],
        "job_card": [r"job cards?", r"work orders?", r"jobs?", r"close(d)?", r"complet(e|ed)"],
        "service_request": [r"service requests?", r"service calls?", r"requests?", r"calls?",
                            r"breakdowns?", r"faults?", r"repairs?", r"complaints?"],
    }),
    "sales": _patterns({
        "quotation": [r"quot(e|es|ation|ations)", r"estimates?", r"proposals?"],
        "invoicing": [r"invoic(e|es|ed|ing)", r"credit notes?", r"billing", r"post(ed|ing)?"],
        "sales_order": [r"sales orders?", r"orders?", r"deliver(y|ies|ed)", r"cancel(led)?"],
        "pricing": [r"price lists?", r"pric(e|es|ing)", r"discounts?", r"rates?"],
        "customer": [r"customers?", r"credit limits?", r"clients?"],
        "sales_report": [r"reports?", r"analysis", r"salesperson", r"export"],
    }),
}


def _detect_intent(text, mode="service"):
    for intent, pattern in _INTENT_PATTERNS[mode].items():
        if pattern.search(text):
            return intent
    return "question"  # nothing on this mode's topic


_OFF_TOPIC = {
    "service": ("This is Service mode, so I can help with the Service module: service "
                "requests, technician scheduling, job cards, spare parts, warranties, "
                "contracts, maintenance and service reports. For the Sales module, switch "
                "to Sales mode, and for anything else, switch to General mode at the top "
                "of the chat - or type 'human' to reach our team."),
    "sales": ("This is Sales mode, so I can help with the Sales module: quotations, sales "
              "orders, invoices, credit notes, pricing, customers and sales reports. For "
              "other questions, switch to Service or General mode at the top of the chat."),
}
_TEAM = {"service": "member of our support team", "sales": "member of our support team"}


# Next questions to offer when the model gives none (or the AI is offline). They
# match the help articles, so clicking one gets an answer even offline.
_S = {
    "log": "How do I log a new service request?",
    "assign": "How do I assign a service request to a technician?",
    "warranty": "How do I check whether equipment is under warranty?",
    "contract": "How do I set up a service contract and its SLA times?",
    "parts": "How do I record spare parts used on a job?",
    "close": "How do I close a service request and bill the customer?",
    "ppm": "How do I set up preventive maintenance visits?",
    "s_reports": "Which service reports are available?",
    "quote": "How do I create a sales quotation?",
    "convert": "How do I convert a quotation into a sales order?",
    "invoice": "How do I create a sales invoice?",
    "credit": "How do I issue a credit note or handle a sales return?",
    "prices": "How do discounts and price lists work?",
    "limit": "What happens when a customer is over their credit limit?",
    "change": "How do I change or cancel a sales order?",
    "x_reports": "Which sales reports are available?",
}
_OFFLINE_SUGGESTIONS = {
    "service": {
        "service_request": ["assign", "warranty", "close"],
        "scheduling": ["parts", "close", "ppm"],
        "job_card": ["parts", "close", "s_reports"],
        "spare_parts": ["close", "warranty", "s_reports"],
        "warranty": ["contract", "log", "assign"],
        "contract": ["warranty", "ppm", "s_reports"],
        "maintenance": ["contract", "assign", "s_reports"],
        "service_billing": ["parts", "contract", "s_reports"],
        "service_report": ["contract", "ppm", "log"],
        "_default": ["log", "assign", "warranty"],
    },
    "sales": {
        "quotation": ["convert", "prices", "invoice"],
        "sales_order": ["invoice", "change", "limit"],
        "invoicing": ["credit", "x_reports", "prices"],
        "pricing": ["quote", "limit", "x_reports"],
        "customer": ["limit", "quote", "x_reports"],
        "sales_report": ["invoice", "credit", "prices"],
        "_default": ["quote", "convert", "invoice"],
    },
}


def _offline_suggestions(intent, mode):
    table = _OFFLINE_SUGGESTIONS.get(mode)
    if not table:
        return []  # General mode can't answer anything offline, so offer nothing
    return [_S[k] for k in table.get(intent, table["_default"])]


def _offline_reply(user_message, faqs, has_files=False, mode="service"):
    sentiment = ("negative" if _NEGATIVE.search(user_message)
                 else "positive" if _POSITIVE.search(user_message) else "neutral")
    wants_human = bool(_HUMAN.search(user_message))

    if mode == "general":
        return AIReply(
            reply=("General answers need the AI assistant, which is offline right now. "
                   "For help with the Service or Sales module, switch to Service mode or "
                   "Sales mode at the top of the chat."),
            intent="question", sentiment=sentiment, source="offline")

    topic = _detect_intent(user_message, mode)
    intent = topic
    team = _TEAM[mode]

    if sentiment == "negative":
        # Keyword matching can't solve a complaint; a FAQ answer here reads as dismissive.
        reply = ("I'm sorry about this. I've opened a priority ticket and a "
                 f"{team} will contact you shortly.")
        escalate = True
    elif wants_human:
        reply = f"Sure - I've created a ticket and a {team} will follow up shortly."
        escalate = True
    elif has_files:
        reply = ("I can read screenshots, photos and files once the AI assistant is "
                 "connected, but it's offline right now. Type 'human' if you'd like our "
                 "team to take a look.")
        escalate = False
    elif faqs:
        reply = faqs[0].answer
        escalate = False
    elif topic == "question":
        # Not this mode's topic: point to the other modes instead of opening a ticket.
        reply = _OFF_TOPIC[mode]
        escalate = False
    else:
        reply = ("I couldn't find an answer to that in our help articles. "
                 "I've passed your question to our support team.")
        escalate = True

    return AIReply(
        reply=reply,
        intent=intent,
        sentiment=sentiment,
        escalate=escalate,
        ticket_subject=user_message[:80] if escalate else "",
        source="offline",
    )
