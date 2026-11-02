"""
Do Not Call: Legal Guidance Tool

One tool:
  - get_legal_remedies   -- Return ordered, step-by-step guidance for filing an FTC
                            complaint, an FCC complaint, and pursuing civil remedy under
                            TCPA (47 U.S.C. § 227); includes state-specific small claims
                            court limits for the top-10 states.

The TCPA civil action step is only included when violation_count >= 2 (repeat/willful).
State small claims limits are read from config/dnc_config.json. For states not in the
map, a default "check your state's website" message is returned.
"""

import json
from pathlib import Path
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import ToolPermission, tool
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _BASE_DIR / "config" / "dnc_config.json"
_TEMPLATES_DIR = _BASE_DIR / "templates"

_DISCLAIMER = (
    "This tool provides general information only and is not legal advice. "
    "Consult a licensed attorney for guidance specific to your situation. "
    "TCPA enforcement outcomes depend on facts and jurisdiction."
)


def _load_config() -> dict:
    return json.loads(_CONFIG_PATH.read_text())


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LegalStep(BaseModel):
    step_number: int
    action: str
    url: str
    description: str
    statute: str

    model_config = {"arbitrary_types_allowed": True}


class LegalGuidance(BaseModel):
    steps: list
    total_potential_damages: int
    small_claims_limit: str
    state: str
    complaint_guide: str
    disclaimer: str

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Tool: get_legal_remedies
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def get_legal_remedies(violation_count: int, state: str) -> LegalGuidance:
    """Return structured, step-by-step legal remedies for Do Not Call Registry violations.

    Always includes FTC and FCC complaint steps. For violation_count >= 2, adds a TCPA
    civil action step with calculated damages and the state's small claims court limit.

    State small claims limits are looked up from config/dnc_config.json for the top 10
    US states (CA, TX, FL, NY, PA, IL, OH, GA, NC, MI). For all other states, a default
    advisory to check the state court's website is returned.

    Statutory damage amounts are read from config — not hard-coded — so they remain
    accurate if the config is updated.

    Args:
        violation_count (int): Total number of documented violations this session.
                                Must be >= 1.
        state (str): Two-letter US state abbreviation (e.g. "CA", "TX"). Used to look
                      up the applicable small claims court dollar limit.
    Returns:
        LegalGuidance: Ordered list of LegalStep objects, total potential damages,
                       state small claims limit, filled FTC complaint guide text,
                       and disclaimer.
    """
    config = _load_config()
    per_call = config["tcpa_damages"]["per_call"]
    total_damages = violation_count * per_call

    state_key = state.upper()[:2]
    raw_limit = config["small_claims_limits"].get(state_key)
    if raw_limit is not None:
        small_claims_limit = f"${raw_limit:,}"
    else:
        small_claims_limit = "Check your state court's website for the current small claims limit."

    ftc_url = config["ftc_complaint_url"]
    fcc_url = config["fcc_complaint_url"]

    # --- Build steps ---
    steps: list[dict] = []

    steps.append({
        "step_number": 1,
        "action": "File a complaint with the FTC",
        "url": ftc_url,
        "description": (
            "File a complaint with the Federal Trade Commission (FTC), which enforces "
            "the National Do Not Call Registry and the Telemarketing Sales Rule (TSR), "
            "16 C.F.R. Part 310. Filing is free and strengthens FTC enforcement records "
            "against repeat violators. Fines on companies can reach $50,120 per violation."
        ),
        "statute": "16 C.F.R. Part 310",
    })

    steps.append({
        "step_number": 2,
        "action": "File a complaint with the FCC",
        "url": fcc_url,
        "description": (
            "File a separate complaint with the Federal Communications Commission (FCC), "
            "which enforces the Telephone Consumer Protection Act (TCPA), "
            "47 U.S.C. § 227, and FCC regulations at 47 C.F.R. § 64.1200. "
            "An FCC complaint is independent of the FTC complaint and targets the "
            "specific TCPA violation. The FCC may forward the complaint to the caller "
            "and open an investigation."
        ),
        "statute": "47 U.S.C. § 227; 47 C.F.R. § 64.1200",
    })

    if violation_count >= 2:
        steps.append({
            "step_number": 3,
            "action": "Pursue TCPA civil action in small claims court",
            "url": "https://www.uscourts.gov/court-locator",
            "description": (
                f"Under 47 U.S.C. § 227(b)(3), you have a private right of action to sue "
                f"the caller in state court without an attorney. "
                f"For {violation_count} documented violations at ${per_call}/call, "
                f"minimum statutory damages are ${total_damages:,}. "
                f"If violations were knowing or willful, the court may award up to "
                f"${violation_count * config['tcpa_damages']['willful_per_call']:,} "
                f"(3× per call under 47 U.S.C. § 227(c)(5)(C)). "
                f"Small claims limit for {state_key}: {small_claims_limit}. "
                "If your claim exceeds the small claims limit, consider a higher court "
                "or a TCPA plaintiff's attorney (many work on contingency)."
            ),
            "statute": "47 U.S.C. § 227(b)(3); 47 U.S.C. § 227(c)(5)",
        })

    # --- Fill complaint guide template ---
    template_path = _TEMPLATES_DIR / "ftc_complaint_steps.txt"
    template_text = template_path.read_text()
    tokens = {
        "caller_number": "the caller's number",
        "state": state_key,
        "small_claims_limit": small_claims_limit,
        "total_damages": f"${total_damages:,}",
        "ftc_complaint_url": ftc_url,
        "fcc_complaint_url": fcc_url,
    }
    complaint_guide = template_text.format(**tokens)

    return LegalGuidance(
        steps=steps,
        total_potential_damages=total_damages,
        small_claims_limit=small_claims_limit,
        state=state_key,
        complaint_guide=complaint_guide,
        disclaimer=_DISCLAIMER,
    )
