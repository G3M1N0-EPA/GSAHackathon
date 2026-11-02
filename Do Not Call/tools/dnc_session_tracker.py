"""
Do Not Call: Session Violation Tracker

One tool:
  - track_violation   -- Append a violation record to the running session list and
                         return the updated list with escalation flag.

There is NO database or file I/O. The violations list lives entirely in the agent's
conversation context. The agent must pass the returned `violations` list back to this
tool on every subsequent call to maintain the running count.
"""

from ibm_watsonx_orchestrate.agent_builder.tools import ToolPermission, tool
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SessionViolationState(BaseModel):
    violations: list
    total_count: int
    should_escalate: bool
    summary: str

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Tool: track_violation
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def track_violation(
    caller_number: str,
    call_date: str,
    call_time: str,
    session_violations: list,
) -> SessionViolationState:
    """Append a new violation to the session list and return the updated state.

    This tool maintains a running in-context list of violations reported during
    the current conversation. Because there is no persistent storage, the agent
    MUST pass the `violations` list returned by this tool back as the
    `session_violations` argument on every subsequent call.

    Escalation is triggered automatically when the total count reaches 2 or more,
    at which point the agent should offer legal remedies in addition to the
    cease-and-desist letter.

    Pass `session_violations=[]` on the very first call in a session.

    Args:
        caller_number (str): Phone number of the caller associated with this violation.
        call_date (str): Date of the call (e.g. "2024-07-15" or "July 15, 2024").
        call_time (str): Time of the call (e.g. "2:34 PM").
        session_violations (list): The running violations list from the previous call.
                                    Pass [] on the first call in a session. The agent
                                    must carry the returned violations list forward in
                                    context for subsequent calls.
    Returns:
        SessionViolationState: Updated violations list, total count, should_escalate
                               flag (True when count >= 2), and a plain-English summary.
    """
    updated = list(session_violations)  # copy to avoid mutating the input
    updated.append({
        "caller_number": caller_number,
        "call_date": call_date,
        "call_time": call_time,
    })

    total = len(updated)
    should_escalate = total >= 2
    escalation_note = " Escalation recommended." if should_escalate else ""
    summary = f"{total} violation{'s' if total != 1 else ''} tracked this session.{escalation_note}"

    return SessionViolationState(
        violations=updated,
        total_count=total,
        should_escalate=should_escalate,
        summary=summary,
    )
