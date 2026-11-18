"""
Do Not Call Agent — Standalone End-to-End Test Suite
=====================================================
Mocks ibm_watsonx_orchestrate so tests run outside the ADK virtualenv.
Run from the repo root:
    python3 "Do Not Call/tests/run_tests.py"
Or from inside the Do Not Call directory:
    python3 tests/run_tests.py
"""

import sys
import types
import pathlib

# ── 0. Inject mock ADK so tools import cleanly ────────────────────────────

def _mock_tool(**kwargs):
    """Identity decorator — returns the function unchanged."""
    def decorator(fn):
        return fn
    return decorator

class _MockToolPermission:
    READ_ONLY = "READ_ONLY"
    READ_WRITE = "READ_WRITE"

# Build the minimal module tree: ibm_watsonx_orchestrate.agent_builder.tools
_wx = types.ModuleType("ibm_watsonx_orchestrate")
_wb = types.ModuleType("ibm_watsonx_orchestrate.agent_builder")
_wt = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools")
_wt.tool = _mock_tool
_wt.ToolPermission = _MockToolPermission
_wx.agent_builder = _wb
_wb.tools = _wt
sys.modules["ibm_watsonx_orchestrate"] = _wx
sys.modules["ibm_watsonx_orchestrate.agent_builder"] = _wb
sys.modules["ibm_watsonx_orchestrate.agent_builder.tools"] = _wt

# ── 1. Resolve tools path ─────────────────────────────────────────────────

_HERE = pathlib.Path(__file__).resolve().parent          # …/Do Not Call/tests/
_TOOLS = _HERE.parent / "tools"                          # …/Do Not Call/tools/
sys.path.insert(0, str(_TOOLS))

# ── 2. Import tools ───────────────────────────────────────────────────────

from dnc_registration import register_number, check_registration_status
from dnc_violation_check import lookup_caller, assess_violation
from dnc_session_tracker import track_violation
from dnc_letter_generator import generate_violation_letter
from dnc_legal_guidance import get_legal_remedies

# ── 3. Helpers ────────────────────────────────────────────────────────────

_passed = 0
_failed = 0

def check(label: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✓  {label}")
    else:
        _failed += 1
        print(f"  ✗  FAIL: {label}" + (f" — {detail}" if detail else ""))

def section(title: str):
    print()
    print(f"── {title} {'─' * max(0, 55 - len(title))}")

# ── 4. Tests ──────────────────────────────────────────────────────────────

print("=" * 60)
print("DO NOT CALL AGENT — END-TO-END TEST SUITE")
print("=" * 60)

# ── REGISTRATION ──────────────────────────────────────────────────────────
section("1. register_number")
reg = register_number(name="Gem Cortez", phone="555-867-5309")
check("phone formatted correctly",     reg.phone == "(555) 867-5309",
      f"got: {reg.phone}")
check("registration_url present",      reg.registration_url.startswith("https://"))
check("effective_date_note present",   "31 days" in reg.effective_date_note)
check("disclaimer present",            len(reg.disclaimer) > 10)
check("instructions mention FTC URL",  reg.registration_url in reg.instructions)
check("11-digit number stripped",
      register_number("T", "15558675309").phone == "(555) 867-5309")

section("2. check_registration_status")
status = check_registration_status(phone="(555) 867-5309")
check("phone formatted correctly",     status.phone == "(555) 867-5309")
check("verify_url present",            status.verify_url.startswith("https://"))
check("registration_note — permanent", "does not expire" in status.registration_note.lower())

# ── VIOLATION CHECK ───────────────────────────────────────────────────────
section("3. lookup_caller — no NUMVERIFY key → graceful warning")
lookup = lookup_caller(phone="888-555-1234")
check("phone digits stored",           lookup.phone == "8885551234")
check("warning message returned",      lookup.warning is not None)
check("NUMVERIFY_API_KEY in warning",  "NUMVERIFY_API_KEY" in (lookup.warning or ""))
check("manual_lookup_url present",     (lookup.manual_lookup_url or "").startswith("https://"))
check("no crash — disclaimer present", len(lookup.disclaimer) > 10)

section("4. assess_violation — VIOLATION (registered ≥31 days, no consent, no exemption)")
v = assess_violation(
    user_registered=True,
    days_since_registration=90,
    caller_type="telemarketer",
    user_consented=False,
    exemption_claimed="none",
)
check("verdict == VIOLATION",          v.verdict == "VIOLATION",          f"got: {v.verdict}")
check("statutes populated",            len(v.statutes) >= 2)
check("next_step mentions C&D",        "cease" in v.recommended_next_step.lower())

section("5. assess_violation — NO_VIOLATION (not registered)")
v2 = assess_violation(
    user_registered=False,
    days_since_registration=0,
    caller_type="telemarketer",
    user_consented=False,
    exemption_claimed="",
)
check("verdict == NO_VIOLATION",       v2.verdict == "NO_VIOLATION",      f"got: {v2.verdict}")

section("6. assess_violation — NO_VIOLATION (user consented)")
v3 = assess_violation(
    user_registered=True,
    days_since_registration=90,
    caller_type="telemarketer",
    user_consented=True,
    exemption_claimed="",
)
check("verdict == NO_VIOLATION",       v3.verdict == "NO_VIOLATION",      f"got: {v3.verdict}")
check("reason mentions consent",       "consent" in v3.reason.lower())

section("7. assess_violation — POSSIBLE_VIOLATION (< 31 days)")
v4 = assess_violation(
    user_registered=True,
    days_since_registration=10,
    caller_type="telemarketer",
    user_consented=False,
    exemption_claimed="",
)
check("verdict == POSSIBLE_VIOLATION", v4.verdict == "POSSIBLE_VIOLATION", f"got: {v4.verdict}")

section("8. assess_violation — POSSIBLE_VIOLATION (charitable exemption)")
v5 = assess_violation(
    user_registered=True,
    days_since_registration=60,
    caller_type="charity",
    user_consented=False,
    exemption_claimed="charitable",
)
check("verdict == POSSIBLE_VIOLATION", v5.verdict == "POSSIBLE_VIOLATION", f"got: {v5.verdict}")
check("reason mentions exemption",     "exemption" in v5.reason.lower())

# ── SESSION TRACKER ───────────────────────────────────────────────────────
section("9. track_violation — first violation (no escalation)")
s1 = track_violation(
    caller_number="8885551234",
    call_date="2025-06-01",
    call_time="2:34 PM",
    session_violations=[],
)
check("count == 1",                    s1.total_count == 1,               f"got: {s1.total_count}")
check("should_escalate == False",      s1.should_escalate == False)
check("violations list has 1 entry",   len(s1.violations) == 1)
check("caller_number stored",          s1.violations[0]["caller_number"] == "8885551234")

section("10. track_violation — second violation (escalation triggered)")
s2 = track_violation(
    caller_number="8885551234",
    call_date="2025-06-08",
    call_time="11:15 AM",
    session_violations=s1.violations,
)
check("count == 2",                    s2.total_count == 2,               f"got: {s2.total_count}")
check("should_escalate == True",       s2.should_escalate == True)
check("violations list has 2 entries", len(s2.violations) == 2)
check("'Escalation' in summary",       "Escalation" in s2.summary)

section("11. track_violation — immutability (original list not mutated)")
check("s1.violations unchanged at 1",  len(s1.violations) == 1)

# ── LETTER GENERATOR ─────────────────────────────────────────────────────
section("12. generate_violation_letter — 1 violation (C&D template)")
letter1 = generate_violation_letter(
    user_name="Gem Cortez",
    user_phone="(555) 867-5309",
    user_email="cortez.gemino@epa.gov",
    caller_name="Robocall Express",
    caller_number="(888) 555-1234",
    call_date="June 1, 2025",
    call_time="2:34 PM",
    violation_count=1,
)
check("statutory_damages == $500",     letter1.statutory_damages == 500,
      f"got: {letter1.statutory_damages}")
check("email_body contains user name", "Gem Cortez" in letter1.email_body)
check("email_body contains caller",    "Robocall Express" in letter1.email_body)
check("email_body contains call date", "June 1, 2025" in letter1.email_body)
check("subject present",               len(letter1.email_subject) > 5)
check("statutes populated",            len(letter1.statutes) >= 1)

section("13. generate_violation_letter — 2 violations (escalation template)")
letter2 = generate_violation_letter(
    user_name="Gem Cortez",
    user_phone="(555) 867-5309",
    user_email="cortez.gemino@epa.gov",
    caller_name="Robocall Express",
    caller_number="(888) 555-1234",
    call_date="June 8, 2025",
    call_time="11:15 AM",
    violation_count=2,
)
check("statutory_damages == $1,000",   letter2.statutory_damages == 1000,
      f"got: {letter2.statutory_damages}")
check("email_body contains caller",    "Robocall Express" in letter2.email_body)
check("different subject from C&D",    letter1.email_subject != letter2.email_subject,
      "subjects identical — check config email_format keys")

# ── LEGAL GUIDANCE ────────────────────────────────────────────────────────
section("14. get_legal_remedies — 1 violation, TX (FTC + FCC only)")
g1 = get_legal_remedies(violation_count=1, state="TX")
check("step count == 2",               len(g1.steps) == 2,                f"got: {len(g1.steps)}")
check("step 1 is FTC",                 "FTC" in g1.steps[0]["action"])
check("step 2 is FCC",                 "FCC" in g1.steps[1]["action"])
check("total_damages == $500",         g1.total_potential_damages == 500)

section("15. get_legal_remedies — 2 violations, CA (adds civil action step)")
g2 = get_legal_remedies(violation_count=2, state="CA")
check("step count == 3",               len(g2.steps) == 3,                f"got: {len(g2.steps)}")
check("step 3 is TCPA civil action",   "TCPA" in g2.steps[2]["action"] or "civil" in g2.steps[2]["action"].lower())
check("total_damages == $1,000",       g2.total_potential_damages == 1000)
check("CA small claims == $12,500",    g2.small_claims_limit == "$12,500",
      f"got: {g2.small_claims_limit}")
check("complaint_guide filled",        "the caller" in g2.complaint_guide.lower())

section("16. get_legal_remedies — unknown state AK (default message)")
g3 = get_legal_remedies(violation_count=2, state="AK")
check("small_claims_limit is advisory", "check" in g3.small_claims_limit.lower()
      or "$" in g3.small_claims_limit)

# ── 5. Summary ────────────────────────────────────────────────────────────

total = _passed + _failed
print()
print("=" * 60)
if _failed == 0:
    print(f"ALL {_passed} ASSERTIONS PASSED  ✓")
else:
    print(f"RESULTS: {_passed}/{total} passed   {_failed} FAILED  ✗")
print("=" * 60)

sys.exit(0 if _failed == 0 else 1)
