"""
Do Not Call: Violation Check Tools

Two tools:
  - lookup_caller     -- Look up a phone number via Numverify API (carrier, line type);
                         gracefully degrades when NUMVERIFY_API_KEY is absent
  - assess_violation  -- Deterministic TCPA/DNC logic tree that returns a verdict of
                         VIOLATION, POSSIBLE_VIOLATION, or NO_VIOLATION based on user Q&A

No LLM involvement in the verdict — all logic is deterministic and data-driven.
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

import requests
from ibm_watsonx_orchestrate.agent_builder.tools import ToolPermission, tool
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "dnc_config.json"
_REQUEST_TIMEOUT = 10  # seconds

_DISCLAIMER = (
    "This tool provides general information only and is not legal advice. "
    "Consult a licensed attorney for guidance specific to your situation."
)

# Exemptions that reduce a call to POSSIBLE_VIOLATION rather than a clear VIOLATION.
# Config-driven so new exemptions can be added without code changes.
_VALID_EXEMPTIONS = [
    "political",
    "charitable",
    "survey",
    "polling",
    "existing_business_relationship",
    "debt_collection",
]


def _load_config() -> dict:
    return json.loads(_CONFIG_PATH.read_text())


def _strip_non_numeric(phone: str) -> str:
    return re.sub(r"\D", "", phone)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CallerLookupResult(BaseModel):
    phone: str
    valid: Optional[bool] = None
    carrier: Optional[str] = None
    line_type: Optional[str] = None
    country_code: Optional[str] = None
    formatted: Optional[str] = None
    warning: Optional[str] = None
    manual_lookup_url: Optional[str] = None
    disclaimer: str

    model_config = {"arbitrary_types_allowed": True}


class ViolationAssessment(BaseModel):
    verdict: str  # VIOLATION | POSSIBLE_VIOLATION | NO_VIOLATION
    reason: str
    statutes: list
    recommended_next_step: str
    disclaimer: str

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Tool 1: lookup_caller
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def lookup_caller(phone: str) -> CallerLookupResult:
    """Look up a caller's phone number to retrieve carrier and line-type information.

    Calls the Numverify API (http://apilayer.net/api/validate) if the
    NUMVERIFY_API_KEY environment variable is set. Returns carrier name, line type
    (mobile/landline/VOIP), country code, and whether the number is valid.

    If NUMVERIFY_API_KEY is absent, returns a graceful warning with a manual FCC
    lookup URL instead of raising an error.

    Network errors (timeouts, connectivity) are caught and returned as warnings
    so the agent can continue the violation assessment flow.

    Args:
        phone (str): The caller's phone number. Non-numeric characters are stripped
                     before lookup.
    Returns:
        CallerLookupResult: Carrier, line type, country code, validity flag, and
                            formatted number; or a warning if the key is absent or
                            the request fails.
    """
    api_key = os.environ.get("NUMVERIFY_API_KEY", "")
    digits = _strip_non_numeric(phone)

    if not api_key:
        return CallerLookupResult(
            phone=digits,
            warning=(
                "NUMVERIFY_API_KEY not set. "
                "Set this env var for live carrier lookup."
            ),
            manual_lookup_url=(
                "https://www.fcc.gov/consumers/guides/stop-unwanted-robocalls-and-texts"
            ),
            disclaimer=_DISCLAIMER,
        )

    config = _load_config()
    url = config.get("numverify_base_url", "http://apilayer.net/api/validate")
    params = {
        "access_key": api_key,
        "number": digits,
        "country_code": "US",
        "format": 1,
    }

    try:
        resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        return CallerLookupResult(
            phone=digits,
            warning=f"Numverify request failed: {exc}. Use the FCC manual lookup instead.",
            manual_lookup_url=(
                "https://www.fcc.gov/consumers/guides/stop-unwanted-robocalls-and-texts"
            ),
            disclaimer=_DISCLAIMER,
        )

    return CallerLookupResult(
        phone=digits,
        valid=data.get("valid"),
        carrier=data.get("carrier"),
        line_type=data.get("line_type"),
        country_code=data.get("country_code"),
        formatted=data.get("international_format") or data.get("local_format"),
        disclaimer=_DISCLAIMER,
    )


# ---------------------------------------------------------------------------
# Tool 2: assess_violation
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def assess_violation(
    user_registered: bool,
    days_since_registration: int,
    caller_type: str,
    user_consented: bool,
    exemption_claimed: str,
) -> ViolationAssessment:
    """Apply the TCPA/DNC logic tree to determine whether a call is a likely violation.

    All logic is deterministic — no LLM is involved in the verdict. The tree is:
      1. Not registered on the DNC Registry → NO_VIOLATION (advise registration).
      2. Registered < 31 days → POSSIBLE_VIOLATION (registration not yet effective).
      3. User provided prior consent → NO_VIOLATION.
      4. Caller claims a recognised exemption (political, charitable, survey, polling,
         existing_business_relationship, debt_collection) → POSSIBLE_VIOLATION.
      5. Registered ≥ 31 days, no consent, no valid exemption → VIOLATION.

    Args:
        user_registered (bool): True if the user's number is on the DNC Registry.
        days_since_registration (int): How many days ago the number was registered.
                                        Pass 0 if unknown or just registered.
        caller_type (str): Free-text description of the caller (e.g. "telemarketer",
                            "charity", "political campaign"). Informational only.
        user_consented (bool): True if the user provided prior written or oral consent
                                to receive calls from this caller.
        exemption_claimed (str): Exemption the caller asserts, if any. Pass an empty
                                  string or "none" when no exemption is claimed. Valid
                                  values: political, charitable, survey, polling,
                                  existing_business_relationship, debt_collection.
    Returns:
        ViolationAssessment: verdict (VIOLATION / POSSIBLE_VIOLATION / NO_VIOLATION),
                             plain-English reason, applicable statutes, and recommended
                             next step.
    """
    statutes = ["47 U.S.C. § 227(c)", "16 C.F.R. Part 310"]
    exemption_lower = (exemption_claimed or "").strip().lower()

    # --- Logic tree ---
    if not user_registered:
        verdict = "NO_VIOLATION"
        reason = (
            "You must register on the DNC Registry before TCPA protections apply."
        )
    elif days_since_registration < 31:
        verdict = "POSSIBLE_VIOLATION"
        reason = (
            "Registration not yet effective (takes 31 days). "
            "Keep the caller's number and call back date."
        )
    elif user_consented:
        verdict = "NO_VIOLATION"
        reason = "You provided prior written or oral consent."
    elif exemption_lower and exemption_lower not in ("none", "n/a", "") and exemption_lower in _VALID_EXEMPTIONS:
        verdict = "POSSIBLE_VIOLATION"
        reason = (
            f"The caller claims a '{exemption_claimed}' exemption. "
            "These have specific legal limits — consult an attorney."
        )
    else:
        verdict = "VIOLATION"
        reason = (
            "You are registered (≥31 days), gave no consent, and no valid exemption "
            "applies. This is a likely TCPA/DNC violation."
        )

    # --- Recommended next step ---
    if verdict == "VIOLATION":
        next_step = "Record the caller details and generate a cease-and-desist email."
    elif verdict == "POSSIBLE_VIOLATION":
        next_step = "Document the call and consult an attorney."
    else:
        next_step = "No action required at this time."

    return ViolationAssessment(
        verdict=verdict,
        reason=reason,
        statutes=statutes,
        recommended_next_step=next_step,
        disclaimer=_DISCLAIMER,
    )
