"""
Do Not Call: Violation Letter Generator

One tool:
  - generate_violation_letter   -- Fill a cease-and-desist or escalation email template
                                   with the user's details and return the complete email,
                                   ready to copy-paste into any email client.

Template selection:
  violation_count == 1  →  templates/cease_and_desist_email.txt
  violation_count >= 2  →  templates/escalation_email.txt

Statutory amounts are read from config/dnc_config.json — not hard-coded.
"""

import datetime
import json
from pathlib import Path

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
    "Consult a licensed attorney for guidance specific to your situation."
)


def _load_config() -> dict:
    return json.loads(_CONFIG_PATH.read_text())


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class GeneratedLetter(BaseModel):
    email_subject: str
    email_body: str
    violation_count: int
    statutory_damages: int
    statutes: list
    disclaimer: str

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Tool: generate_violation_letter
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def generate_violation_letter(
    user_name: str,
    user_phone: str,
    user_email: str,
    caller_name: str,
    caller_number: str,
    call_date: str,
    call_time: str,
    violation_count: int,
) -> GeneratedLetter:
    """Generate a cease-and-desist or escalation email ready to send to the caller.

    Selects the appropriate template based on violation_count:
      - 1 violation  → cease_and_desist_email.txt (first formal notice)
      - 2+ violations → escalation_email.txt (repeat/willful violation notice with
                         statutory damage demand)

    All template tokens ({user_name}, {caller_number}, etc.) are filled from the
    provided arguments. Statutory damage amounts are read from config/dnc_config.json
    so they can be updated without code changes.

    The returned email_body is a complete, formatted email including the subject line,
    date, body, signature, and disclaimer. The user should copy it into their email
    client and send it to the caller's known contact address.

    Args:
        user_name (str): Full name of the person sending the letter.
        user_phone (str): User's phone number (the number that was called).
        user_email (str): User's email address (for reply-to in the letter body).
        caller_name (str): Name or company name of the caller (use "Unknown Caller"
                            if not identified).
        caller_number (str): Phone number the unwanted call came from.
        call_date (str): Date the call occurred (e.g. "July 15, 2024").
        call_time (str): Time the call occurred (e.g. "2:34 PM").
        violation_count (int): Total number of violations tracked this session.
                                Must be >= 1.
    Returns:
        GeneratedLetter: Complete email subject and body, violation count, computed
                         statutory damages, applicable statutes, and disclaimer.
    """
    config = _load_config()
    per_call = config["tcpa_damages"]["per_call"]
    total_damages = violation_count * per_call

    today_date = datetime.date.today().strftime("%B %d, %Y")

    # Select template and subject
    if violation_count >= 2:
        template_name = "escalation_email.txt"
        email_subject = config["email_format"]["subject_repeat"]
    else:
        template_name = "cease_and_desist_email.txt"
        email_subject = config["email_format"]["subject_first"]

    template_path = _TEMPLATES_DIR / template_name
    template_text = template_path.read_text()

    tokens = {
        "user_name": user_name,
        "user_phone": user_phone,
        "user_email": user_email,
        "caller_name": caller_name,
        "caller_number": caller_number,
        "call_date": call_date,
        "call_time": call_time,
        "today_date": today_date,
        "violation_count": violation_count,
        "total_damages": f"${total_damages:,}",
        "ftc_complaint_url": config["ftc_complaint_url"],
    }

    email_body = template_text.format(**tokens)

    return GeneratedLetter(
        email_subject=email_subject,
        email_body=email_body,
        violation_count=violation_count,
        statutory_damages=total_damages,
        statutes=["47 U.S.C. § 227(c)(5)", "16 C.F.R. § 310.4(b)"],
        disclaimer=_DISCLAIMER,
    )
