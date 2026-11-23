"""Scored test set for ERP questions: how accurately does the assistant answer?

Each question in erp_questions.json comes with made-up ERP data (so no real business
data is needed or stored here), the lookups the assistant should call and the facts
its answer must contain. The script asks the live model (Gemini) each question, feeds
it the made-up data instead of querying the ERP, and scores the answer:

  lookups  - did it call the right lookup(s)?              (40% of the score)
  facts    - does the answer contain the expected facts,   (60% of the score)
             and none of the forbidden ones (invented data)?

A question passes when both parts are perfect. Run from the backend folder:

    python -m evals.run_erp_eval                # all questions
    python -m evals.run_erp_eval --only blocked # one question by id
    python -m evals.run_erp_eval --min 80       # exit code 1 if the score is below 80%

It needs GEMINI_API_KEY (from .env or the environment) and uses a few dozen model calls.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

CASES_FILE = Path(__file__).with_name("erp_questions.json")
LOOKUP_WEIGHT, FACT_WEIGHT = 0.4, 0.6
RETRIES, RETRY_PAUSE = 3, 10   # when the model is busy (e.g. Gemini "503 high demand")


class ModelUnavailable(Exception):
    """The live model didn't answer (overloaded, quota): not the assistant's mistake."""


def load_cases(path=CASES_FILE):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("question ids must be unique")
    return cases


def normalize(text):
    """Lower case, straight apostrophes, no thousands separators, single spaces."""
    text = (text or "").lower().replace("’", "'")
    text = re.sub(r"(?<=\d)[,  ](?=\d{3}\b)", "", text)
    return re.sub(r"\s+", " ", text)


def _alternatives(expected):
    return expected if isinstance(expected, list) else [expected]


def score_case(case, answer, called):
    """Score one answer. `called` is the list of lookup names the model called."""
    called = set(called)
    slots = case.get("expect_lookups", [])
    missing_lookups = [s for s in slots if not called & set(_alternatives(s))]
    lookup_score = 1.0 if not slots else 1 - len(missing_lookups) / len(slots)

    text = normalize(answer)
    facts = case.get("expect_facts", [])
    missing_facts = [f for f in facts
                     if not any(normalize(alt) in text for alt in _alternatives(f))]
    invented = [f for f in case.get("forbid_facts", []) if normalize(f) in text]
    fact_score = 1.0 if not facts else 1 - len(missing_facts) / len(facts)
    if invented:
        fact_score = 0.0

    score = LOOKUP_WEIGHT * lookup_score + FACT_WEIGHT * fact_score
    return {"id": case["id"], "score": round(score, 3),
            "passed": lookup_score == 1.0 and fact_score == 1.0,
            "called": sorted(called), "missing_lookups": missing_lookups,
            "missing_facts": missing_facts, "invented": invented}


def summarize(results):
    skipped = sum(bool(r.get("skipped")) for r in results)
    results = [r for r in results if not r.get("skipped")]
    total = len(results)
    passed = sum(r["passed"] for r in results)
    score = sum(r["score"] for r in results) / total * 100 if total else 0.0
    return {"questions": total, "passed": passed, "score_percent": round(score, 1), "skipped": skipped}


def run_case(case, ask, retries=RETRIES, pause=RETRY_PAUSE):
    """Ask one question with the case's made-up data standing in for the ERP.

    `ask(question, fake_lookup)` returns (answer_text, called_lookup_names).
    """
    fake = case.get("fake", {})

    def fake_lookup(name, args):
        if name in fake:
            return fake[name]
        return {"error": "This lookup has no data in this test."}

    for attempt in range(retries):
        try:
            answer, called = ask(case["question"], fake_lookup)
            break
        except ModelUnavailable as exc:   # busy model: wait and ask again, else leave it out
            if attempt == retries - 1:
                return {"id": case["id"], "skipped": True, "passed": False, "score": 0.0,
                        "answer": f"(model unavailable: {exc})"}
            time.sleep(pause)
        except Exception as exc:   # any other failure scores zero instead of stopping the run
            answer, called = f"(error: {exc})", []
            break
    result = score_case(case, answer, called)
    result["answer"] = answer
    return result


def gemini_ask(app):
    """An `ask` that goes through the real chat code (stream_reply) with Gemini."""
    from app.services import ai_service, erp_tools

    def ask(question, fake_lookup):
        called = []

        def lookup(name, args):
            called.append(name)
            return fake_lookup(name, args)

        real = erp_tools.run_lookup
        erp_tools.run_lookup = lookup
        try:
            with app.app_context():
                reply = None
                for kind, value in ai_service.stream_reply([], question, mode="service",
                                                           erp_lookups=True):
                    if kind == "done":
                        reply = value
        finally:
            erp_tools.run_lookup = real
        if reply is None or reply.source != "gemini":   # fell back to the offline answers
            raise ModelUnavailable("Gemini didn't answer (busy or out of quota)")
        return reply.reply, called

    return ask


def make_app():
    from dotenv import load_dotenv
    load_dotenv()
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY is not set (add it to backend/.env).")
    from app import create_app
    from app.config import Config, TestConfig

    class EvalConfig(TestConfig):
        AI_PROVIDER = "gemini"
        AI_ENABLED = True
        GEMINI_MODEL = Config.GEMINI_MODEL
        GEMINI_FALLBACK_MODEL = getattr(Config, "GEMINI_FALLBACK_MODEL", "")
        ERP_COMPANY = "TEST_COMPANY"

    return create_app(EvalConfig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", help="run one question by id")
    parser.add_argument("--min", type=float, default=0, help="fail below this score (percent)")
    parser.add_argument("--json", help="also write the full results to this file")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
        if not cases:
            sys.exit(f"No question with id {args.only!r}.")
    ask = gemini_ask(make_app())

    results = []
    for case in cases:
        r = run_case(case, ask)
        results.append(r)
        if r.get("skipped"):
            print(f"SKIP           {r['id']}  (model unavailable, not scored)")
            continue
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['score'] * 100:5.1f}%  {r['id']}")
        if not r["passed"]:
            if r["missing_lookups"]:
                print(f"        lookups missing: {r['missing_lookups']} (called {r['called']})")
            if r["missing_facts"]:
                print(f"        facts missing:   {r['missing_facts']}")
            if r["invented"]:
                print(f"        invented:        {r['invented']}")

    summary = summarize(results)
    print(f"\n{summary['passed']}/{summary['questions']} passed, "
          f"score {summary['score_percent']}%"
          + (f" ({summary['skipped']} skipped: model unavailable)" if summary["skipped"] else ""))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "results": results}, indent=2),
                                   encoding="utf-8")
    return 1 if summary["score_percent"] < args.min or not summary["questions"] else 0


if __name__ == "__main__":
    sys.exit(main())
