"""The scored ERP test set: the questions file is valid and the scoring is right."""
from evals import run_erp_eval as ev
from app.services import erp_tools


def test_question_set_is_valid():
    cases = ev.load_cases()
    assert len(cases) >= 15
    for c in cases:
        assert c["question"] and c["expect_facts"], c["id"]
        for slot in c["expect_lookups"]:
            for name in ev._alternatives(slot):
                assert name in erp_tools.LOOKUPS, (c["id"], name)
        for name in c.get("fake", {}):
            assert name in erp_tools.LOOKUPS, (c["id"], name)


def test_question_set_has_no_real_ids():
    # The repo is public: only made-up numbers (C9xxxx, SC9xxxx, SI9xxx).
    text = ev.CASES_FILE.read_text(encoding="utf-8")
    import re
    for n in re.findall(r"\b(?:SC|SI|C)\d+\b", text):
        assert re.match(r"(?:SC|SI|C)9\d+$", n), n


def test_normalize_drops_thousands_separators():
    assert ev.normalize("KES 1,234,567 and 45 000 — It’s") == "kes 1234567 and 45000 — it's"


CASE = {"id": "x", "expect_lookups": ["a", ["b", "c"]], "expect_facts": ["58", ["1234567", "1.2 million"]],
        "forbid_facts": ["ksh"]}


def test_perfect_answer_passes():
    r = ev.score_case(CASE, "There are 58 active, worth 1,234,567 a year.", ["a", "c"])
    assert r["passed"] and r["score"] == 1.0


def test_partial_answer_is_scored():
    r = ev.score_case(CASE, "There are 58 active.", ["a"])
    assert not r["passed"]
    assert r["missing_lookups"] == [["b", "c"]] and r["missing_facts"] == [["1234567", "1.2 million"]]
    assert r["score"] == round(0.4 * 0.5 + 0.6 * 0.5, 3)


def test_invented_data_zeroes_the_facts():
    r = ev.score_case(CASE, "58 active, 1.2 million, balance Ksh 300", ["a", "b"])
    assert r["invented"] == ["ksh"] and r["score"] == 0.4 and not r["passed"]


def test_run_case_uses_fake_data_and_survives_errors():
    case = {"id": "y", "question": "Q?", "expect_lookups": ["find_customers"], "expect_facts": ["Test Bank"],
            "fake": {"find_customers": {"customers": [{"name": "Test Bank"}]}}}

    def ask(question, lookup):
        data = lookup("find_customers", {})
        assert "error" in lookup("blocked_customers", {})
        return f"It is {data['customers'][0]['name']}.", ["find_customers"]

    assert ev.run_case(case, ask)["passed"]

    def broken(question, lookup):
        raise RuntimeError("quota")

    r = ev.run_case(case, broken)
    assert r["score"] == 0 and "quota" in r["answer"]


def test_summary():
    s = ev.summarize([{"passed": True, "score": 1.0}, {"passed": False, "score": 0.5}])
    assert s == {"questions": 2, "passed": 1, "score_percent": 75.0, "skipped": 0}
    s = ev.summarize([{"passed": True, "score": 1.0}, {"skipped": True, "passed": False, "score": 0.0}])
    assert s["score_percent"] == 100.0 and s["skipped"] == 1


def test_busy_model_is_retried_then_skipped():
    case = {"id": "z", "question": "Q?", "expect_lookups": [], "expect_facts": ["ok"]}
    tries = []

    def busy_then_ok(question, lookup):
        tries.append(1)
        if len(tries) < 2:
            raise ev.ModelUnavailable("503")
        return "ok", []

    assert ev.run_case(case, busy_then_ok, pause=0)["passed"] and len(tries) == 2

    def always_busy(question, lookup):
        raise ev.ModelUnavailable("503")

    assert ev.run_case(case, always_busy, pause=0)["skipped"]
