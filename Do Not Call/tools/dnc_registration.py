"""
Do Not Call: Registration Tools

Two tools:
  - register_number              -- Walk user through FTC DNC Registry registration
  - check_registration_status   -- Return FTC verification URL and registration notes

The FTC has no public write API for the Do Not Call Registry. Both tools return
the appropriate FTC URL plus step-by-step instructions for the user to complete
the action in their browser.
"""

import json
import re
from pathlib import Path

from ibm_watsonx_orchestrate.agent_builder.tools import ToolPermission, tool
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "dnc_config.json"

_DISCLAIMER = (
    "This tool provides general information only and is not legal advice. "
    "Consult a licensed attorney for guidance specific to your situation."
)


def _load_config() -> dict:
    return json.loads(_CONFIG_PATH.read_text())


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegistrationInfo(BaseModel):
    name: str
    phone: str
    registration_url: str
    instructions: str
    effective_date_note: str
    disclaimer: str

    model_config = {"arbitrary_types_allowed": True}


class RegistrationStatusInfo(BaseModel):
    phone: str
    verify_url: str
    instructions: str
    registration_note: str
    disclaimer: str

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_phone(raw: str) -> str:
    """Strip all non-numeric characters and return the 10-digit result (or raise)."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(
            f"Phone number must contain exactly 10 digits (got {len(digits)} after stripping)."
        )
    return digits


def _format_phone(digits: str) -> str:
    """Format a 10-digit string as (XXX) XXX-XXXX."""
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


# ---------------------------------------------------------------------------
# Tool 1: register_number
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def register_number(name: str, phone: str) -> RegistrationInfo:
    """Walk the user through registering their phone number on the FTC Do Not Call Registry.

    The FTC does not expose a public write API for DNC registration, so this tool
    validates the phone number, formats it, and returns the direct FTC registration URL
    along with step-by-step instructions the user should follow in their browser.

    Args:
        name (str): Full name of the person registering the number.
        phone (str): US phone number to register. Non-numeric characters are stripped;
                     the number must resolve to 10 digits.
    Returns:
        RegistrationInfo: Formatted phone, FTC registration URL, browser instructions,
                          31-day effective-date note, and disclaimer.
    """
    digits = _normalize_phone(phone)
    formatted = _format_phone(digits)
    config = _load_config()

    instructions = (
        "To register your phone number on the National Do Not Call Registry:\n"
        f"  1. Visit {config['ftc_registration_url']} in your web browser.\n"
        "  2. Click 'Register a Phone Number'.\n"
        f"  3. Enter your phone number: {formatted}\n"
        "  4. Enter your email address (required for confirmation).\n"
        "  5. Check your email inbox for a confirmation message from the FTC "
        "and click the verification link within 72 hours.\n"
        "  6. Once confirmed, your number will be active on the registry within 31 days."
    )

    return RegistrationInfo(
        name=name,
        phone=formatted,
        registration_url=config["ftc_registration_url"],
        instructions=instructions,
        effective_date_note=(
            "Registration becomes effective within 31 days per 16 C.F.R. § 310.4(b)(1)(iii)."
        ),
        disclaimer=_DISCLAIMER,
    )


# ---------------------------------------------------------------------------
# Tool 2: check_registration_status
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def check_registration_status(phone: str) -> RegistrationStatusInfo:
    """Return the FTC verification URL so the user can confirm their DNC registration status.

    The FTC does not expose a public query API for registration status. This tool
    formats the phone number and returns the FTC verification URL with instructions
    the user should follow in their browser to confirm registration.

    Args:
        phone (str): US phone number to check. Non-numeric characters are stripped;
                     the number must resolve to 10 digits.
    Returns:
        RegistrationStatusInfo: Formatted phone, FTC verify URL, browser instructions,
                                registration permanence note, and disclaimer.
    """
    digits = _normalize_phone(phone)
    formatted = _format_phone(digits)
    config = _load_config()

    return RegistrationStatusInfo(
        phone=formatted,
        verify_url=config["ftc_verify_url"],
        instructions=(
            "Visit the URL above, enter your phone number and email to verify registration."
        ),
        registration_note=(
            "Registration does not expire. It remains active until the number is "
            "disconnected, reassigned, or you remove it."
        ),
        disclaimer=_DISCLAIMER,
    )
