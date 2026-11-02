# Do Not Call — Project Plan

## Top-Level Overview

**Goal:** Build a standalone watsonx Orchestrate agent project called "Do Not Call" that helps a
user (1) register their phone number on the FTC National Do Not Call Registry, (2) determine
whether an incoming call constitutes a TCPA/DNC violation using a phone-number lookup plus
guided Q&A, (3) generate a cease-and-desist / violation notice letter to send by **email**
to the caller, and (4) provide step-by-step instructions to file a formal FTC complaint and
pursue civil remedy under 47 U.S.C. § 227 (TCPA) for repeat violations.

**Stack:** Same as the existing GSAHackathon project — watsonx Orchestrate native agent (YAML),
Python `@tool` functions with Pydantic return types, no persistent storage (user supplies name
and number each session).

**Project location:**
- Local folder: `Do Not Call/`  (subfolder inside the existing GSAHackathon repo)
- GitHub branch: `Do Not Call`

**Confirmed design decisions:**
- Location: subfolder of GSAHackathon repo, not a separate repo
- Letter delivery: **email only** (cease-and-desist letter output is formatted for email)
- Violation tracking: **session-scoped accumulator** — agent tracks all violations reported
  within the same conversation and escalates automatically when count reaches 2+

**Scope boundary (non-goals):**
- No persistent database of callers or violations across sessions
- No automated dialing, SMS, or real-time call interception
- No actual email sending — tool produces the email-ready text; user copies and sends
- No legal representation — agent produces letters and guidance only, with clear disclaimers

---

## Architecture Overview

```
GSAHackathon/
└── Do Not Call/                         ← New subfolder (on "Do Not Call" branch)
    ├── agents/
    │   └── do_not_call_agent.yaml       ← wxO native agent + tool list + instructions
    ├── tools/
    │   ├── dnc_registration.py          ← Tools: register_number, check_registration_status
    │   ├── dnc_violation_check.py       ← Tools: lookup_caller, assess_violation
    │   ├── dnc_letter_generator.py      ← Tool: generate_violation_letter
    │   ├── dnc_session_tracker.py       ← Tool: track_violation (session accumulator)
    │   └── dnc_legal_guidance.py        ← Tool: get_legal_remedies
    ├── config/
    │   └── dnc_config.json              ← API base URLs, statutory amounts, email format
    ├── templates/
    │   ├── cease_and_desist_email.txt   ← Email body template (first violation)
    │   ├── escalation_email.txt         ← Email body template (repeat/willful violation)
    │   └── ftc_complaint_steps.txt      ← Complaint steps template
    └── README.md                        ← Setup, env vars, usage
```

---

## Sub-Tasks

---

### Sub-Task 0 — Create and Push "Do Not Call" Branch

**Intent:** Create the `Do Not Call` branch from `main` on GitHub before any files are
added, so all subsequent sub-tasks commit to the correct branch.

**Expected Outcomes:**
- Local git branch `Do Not Call` exists and is checked out
- Branch is pushed to GitHub (empty or with initial commit)

**Todo List:**
1. Run `git checkout -b "Do Not Call"` from the GSAHackathon repo root
2. Push the branch: `git push -u origin "Do Not Call"`
3. Confirm branch appears on GitHub before proceeding to Sub-Task 1

**Status:** [x] done

---

### Sub-Task 1 — Project Scaffold and Config

**Intent:** Create the `Do Not Call/` subfolder with all static files (config, templates,
README skeleton) so every subsequent sub-task has what it needs to reference.

**Expected Outcomes:**
- `Do Not Call/` subfolder exists with the full directory structure
- `config/dnc_config.json` has all URLs, statutory amounts, and email format config
- Both email letter templates and the FTC steps template are written with placeholder tokens
- Skeleton `README.md` is in place

**Todo List:**
1. Create `Do Not Call/` with subfolders `agents/`, `tools/`, `config/`, `templates/`
2. Write `Do Not Call/config/dnc_config.json` with:
   - `ftc_registration_url`: `https://www.donotcall.gov/register.html`
   - `ftc_verify_url`: `https://www.donotcall.gov/verify.html`
   - `ftc_complaint_url`: `https://reportfraud.ftc.gov/`
   - `fcc_complaint_url`: `https://consumercomplaints.fcc.gov/hc/en-us`
   - `numverify_base_url`: `http://apilayer.net/api/validate`
   - `tcpa_damages`: `{ "per_call": 500, "willful_per_call": 1500 }`
   - `email_format`: `{ "subject_first": "Cease and Desist — Do Not Call Violation",
     "subject_repeat": "TCPA Violation Notice — Demand for Statutory Damages" }`
   - `small_claims_limits`: top-10-state map (e.g., CA: 12500, TX: 20000, FL: 8000 …)
3. Write `Do Not Call/templates/cease_and_desist_email.txt` with tokens:
   `{user_name}`, `{user_phone}`, `{user_email}`, `{caller_name}`, `{caller_number}`,
   `{call_date}`, `{call_time}`, `{today_date}` — formal first-violation email
4. Write `Do Not Call/templates/escalation_email.txt` — repeat-violation email with same
   tokens plus `{violation_count}`, `{total_damages}`, `{ftc_complaint_url}`
5. Write `Do Not Call/templates/ftc_complaint_steps.txt` — numbered steps for FTC + FCC
   filing and TCPA small claims court action with `{caller_number}`, `{state}`,
   `{small_claims_limit}`, `{total_damages}` tokens
6. Write skeleton `Do Not Call/README.md` (full content added in Sub-Task 7)
7. Commit scaffold to `Do Not Call` branch

**Relevant Context:**
- Config JSON pattern mirrors `GSAHackathon/config/scoring_weights.json`
- Template token convention: `{snake_case}` placeholders filled by `dnc_letter_generator.py`

**Status:** [x] done

---

### Sub-Task 2 — DNC Registration Tools (`dnc_registration.py`)

**Intent:** Give the agent two tools: one to walk the user through registering their number
on the FTC DNC Registry, and one to check whether their number is already registered.

**Expected Outcomes:**
- `register_number(name, phone)` tool returns a structured result with the direct registration
  URL, instructions, and a confirmation note (FTC does not expose a write API, so the tool
  generates the pre-filled URL and instructs the user)
- `check_registration_status(phone)` tool returns the FTC verification URL for the number
  and instructs the user to visit it, with a note that registration takes up to 31 days to
  be effective

**Todo List:**
1. Create `Do Not Call/tools/dnc_registration.py`
2. Define Pydantic response models: `RegistrationInfo`, `RegistrationStatusInfo`
3. Implement `register_number(name: str, phone: str) -> RegistrationInfo`:
   - Validates US phone format (10 digits, strips non-numeric)
   - Returns FTC registration URL with instructions
   - Includes 31-day effective date note and TCPA citation
4. Implement `check_registration_status(phone: str) -> RegistrationStatusInfo`:
   - Returns FTC verification URL: `https://www.donotcall.gov/verify.html`
   - Instructs user to enter number and email to verify registration
   - Includes note: registration lasts until the user removes it or the number is
     disconnected/reassigned
5. Apply `@tool(permission=ToolPermission.READ_ONLY)` to both functions
6. Write docstrings in the established project format (summary → behavior → Args → Returns)

**Relevant Context:**
- Pattern from `tools/gsa_gc_external_evidence.py` — graceful handling when external action
  required by user (returns instructions instead of failing)
- FTC does not expose a public registration write API — the tool provides the URL workflow

**Status:** [x] done

---

### Sub-Task 3 — Violation Check Tools (`dnc_violation_check.py`)

**Intent:** Give the agent tools to look up a caller's phone number (carrier, registration
status, STIR/SHAKEN attestation) and assess whether a call likely constitutes a DNC violation
based on Q&A answers from the user.

**Expected Outcomes:**
- `lookup_caller(phone)` tool returns carrier name, line type (mobile/landline/VOIP), and
  Numverify data; gracefully degrades if the `NUMVERIFY_API_KEY` env var is not set
- `assess_violation(user_registered, days_since_registration, caller_type, consented,
  exemption_claimed)` tool returns a structured verdict — `VIOLATION`, `POSSIBLE_VIOLATION`,
  or `NO_VIOLATION` — with reasoning and applicable TCPA statute sections

**Todo List:**
1. Create `Do Not Call/tools/dnc_violation_check.py`
2. Define Pydantic models: `CallerLookupResult`, `ViolationAssessment`
3. Implement `lookup_caller(phone: str) -> CallerLookupResult`:
   - Calls Numverify API if `NUMVERIFY_API_KEY` is set; returns carrier, line type, country
   - If key absent: returns a warning with the manual lookup URL and FCC complaint tip line
   - Normalizes phone to E.164 format before lookup
4. Implement `assess_violation(user_registered: bool, days_since_registration: int,
   caller_type: str, user_consented: bool, exemption_claimed: str) -> ViolationAssessment`:
   - Applies TCPA/DNC logic tree:
     - Not registered → `NO_VIOLATION` (advise registration)
     - Registered < 31 days → `POSSIBLE_VIOLATION` (registration not yet effective)
     - Registered ≥ 31 days, no consent, no valid exemption → `VIOLATION`
     - Registered, consent given (written/oral) → `NO_VIOLATION`
     - Political, charitable, survey, or existing business relationship exemption →
       `POSSIBLE_VIOLATION` with explanation
   - Attaches relevant statute: 47 U.S.C. § 227(c), 16 C.F.R. Part 310
5. Apply `@tool(permission=ToolPermission.READ_ONLY)` to both tools
6. Read API base URL from `config/dnc_config.json`

**Relevant Context:**
- Graceful-degradation pattern from `gsa_gc_search_litigation` (returns warning when token
  absent, not an error)
- Violation logic tree must be data-driven, not hard-coded if/else — use a config-driven
  exemption list

**Status:** [x] done

---

### Sub-Task 4 — Session Violation Tracker (`dnc_session_tracker.py`)

**Intent:** Give the agent a stateful-within-session tool to accumulate violations reported
during a single conversation. This enables automatic escalation from C&D letter to legal
remedies when the count reaches 2+.

**Expected Outcomes:**
- `track_violation(caller_number, call_date, call_time, session_violations)` returns an
  updated violations list and a `should_escalate` flag (True when count >= 2)
- The agent passes the running list back on every call (session state lives in the
  conversation context, not a database)

**Todo List:**
1. Create `Do Not Call/tools/dnc_session_tracker.py`
2. Define Pydantic models: `ViolationRecord`, `SessionViolationState`
3. Implement `track_violation(caller_number: str, call_date: str, call_time: str,
   session_violations: list[dict]) -> SessionViolationState`:
   - Appends new `ViolationRecord` to the passed-in list
   - Returns updated list, total count, `should_escalate` (count >= 2),
     and a plain-English summary (e.g. "3 violations tracked this session")
4. Apply `@tool(permission=ToolPermission.READ_ONLY)`
5. Docstring must explain that `session_violations` is the running list the agent
   maintains in context — agent must pass it back on every call

**Relevant Context:**
- No database or file I/O — list is passed in and returned (functional/stateless tool)
- Agent instructions (Sub-Task 6) must explicitly tell the LLM to carry the list forward

---

### Sub-Task 5 — Letter Generator Tool (`dnc_letter_generator.py`)

**Intent:** Give the agent a tool to generate a cease-and-desist / violation notice
**email** pre-filled with the user's details and the caller's information.

**Expected Outcomes:**
- `generate_violation_letter(user_name, user_phone, user_email, caller_name, caller_number,
  call_date, call_time, violation_count)` returns a complete email (subject + body), ready
  to copy-paste into any email client
- For `violation_count == 1` it generates a cease-and-desist email from `cease_and_desist_email.txt`
- For `violation_count >= 2` it generates an escalation email from `escalation_email.txt`
  citing TCPA statutory damages

**Todo List:**
1. Create `Do Not Call/tools/dnc_letter_generator.py`
2. Define Pydantic model: `GeneratedLetter` with fields: `email_subject`, `email_body`,
   `violation_count`, `statutory_damages`, `disclaimer`
3. Implement `generate_violation_letter(user_name: str, user_phone: str, user_email: str,
   caller_name: str, caller_number: str, call_date: str, call_time: str,
   violation_count: int) -> GeneratedLetter`:
   - Load appropriate template from `templates/` based on `violation_count`
   - Compute `today_date` (datetime.date.today())
   - Compute `total_damages` = `violation_count × $500` (from config)
   - Fill all `{token}` placeholders
   - Set `email_subject` from `config/dnc_config.json` `email_format` based on violation count
   - Append statutory citations: 47 U.S.C. § 227(c)(5), 16 C.F.R. § 310.4(b)
   - Return complete email plus disclaimer
4. Apply `@tool(permission=ToolPermission.READ_ONLY)`
5. Template path resolved relative to the tool file's directory

**Relevant Context:**
- Disclaimer pattern from `_DISCLAIMER` constant in `tools/gsa_gc_external_evidence.py`
- Statutory amounts read from config, not hard-coded (same as scoring_weights.json pattern)

**Status:** [x] done

---

### Sub-Task 6 — Legal Guidance Tool (`dnc_legal_guidance.py`)

**Intent:** For repeat or willful violations, give the agent a tool that returns structured,
step-by-step guidance for filing an FTC complaint, an FCC complaint, and pursuing civil
remedy (small claims court) under TCPA.

**Expected Outcomes:**
- `get_legal_remedies(violation_count, state)` returns an ordered list of actions with
  URLs, filing fees, damage amounts, and state-specific small claims court limits
- Clearly separated paths: FTC complaint, FCC complaint, TCPA civil action

**Todo List:**
1. Create `Do Not Call/tools/dnc_legal_guidance.py`
2. Define Pydantic models: `LegalStep`, `LegalGuidance`
3. Implement `get_legal_remedies(violation_count: int, state: str) -> LegalGuidance`:
   - Always includes FTC complaint step (URL from config)
   - Always includes FCC complaint step (URL from config)
   - For `violation_count >= 2`: adds TCPA civil action steps:
     - Calculate potential damages: `violation_count × $500` (or × $1,500 if willful)
     - Note: TCPA allows private right of action in state court (47 U.S.C. § 227(b)(3))
     - Reference state small claims limit for the given state (top 10 states hard-coded
       in config; default to "check your state's small claims court limit" for others)
   - Attach disclaimer
4. Apply `@tool(permission=ToolPermission.READ_ONLY)`
5. Include FCC's `consumercomplaints.fcc.gov` URL from config

**Relevant Context:**
- Same structured-output pattern as `ScoreResult` in `gsa_gc_risk_scoring.py`
- Disclaimer pattern from `gsa_gc_external_evidence.py`

**Status:** [x] done

---

### Sub-Task 7 — Agent YAML (`do_not_call_agent.yaml`)

**Intent:** Wire all 7 tools into a watsonx Orchestrate native agent with detailed
instructions that guide the LLM through the correct multi-turn conversation flow, including
session-scoped violation tracking and automatic escalation.

**Expected Outcomes:**
- `agents/do_not_call_agent.yaml` is valid and references all 7 tool names correctly
- Agent instructions define four distinct conversation modes with clear escalation logic
- Welcome message and 5 starter prompts are set

**Todo List:**
1. Create `Do Not Call/agents/do_not_call_agent.yaml`
2. Set `spec_version: v1`, `kind: native`, `llm: groq/openai/gpt-oss-120b`,
   `style: react_core`
3. List all 7 tools: `register_number`, `check_registration_status`, `lookup_caller`,
   `assess_violation`, `track_violation`, `generate_violation_letter`, `get_legal_remedies`
4. Write `instructions:` section with six sub-sections:
   - **Mode detection:** infer intent from first message (register / check / letter / remedies)
   - **Registration flow:** collect name + phone → `register_number` → `check_registration_status`
   - **Violation check flow:** collect caller number → `lookup_caller` → ask 4 Q&A
     (registered ≥31 days? consented? exemption? call purpose?) → `assess_violation`
     → always call `track_violation` to update session list → if VIOLATION, offer letter
   - **Letter flow:** collect `user_email` + missing call details → `generate_violation_letter`
     → after letter, if `violation_count >= 2` automatically offer legal remedies
   - **Remedies flow:** collect state → `get_legal_remedies`
   - **Session tracking rule:** agent MUST pass the current `session_violations` list
     to every `track_violation` call and carry the returned list forward in context
   - **Tone and disclaimer:** plain-English, never legal advice, always show disclaimer
5. Set `welcome_content` and 5 `starter_prompts`

**Relevant Context:**
- Mirror structure of `agents/gsa_gc_federal_ai_agent.yaml`
- Tool names in YAML must exactly match Python function names

**Status:** [x] done

---

### Sub-Task 8 — README and Final Commit

**Intent:** Document the project, env vars, and usage; push all files to the `Do Not Call`
branch on GitHub.

**Expected Outcomes:**
- `Do Not Call/README.md` fully documents setup, env vars, and example conversations
- All files are committed to the `Do Not Call` branch and pushed to GitHub

**Todo List:**
1. Write `Do Not Call/README.md`:
   - Project overview and legal disclaimer
   - Env var table: `NUMVERIFY_API_KEY` (optional, graceful degradation without it)
   - Setup: `pip install -r requirements.txt`, `orchestrate env activate`
   - How to import the agent: `orchestrate agents import ...`
   - Example conversations for each of the four modes
2. Run `git add "Do Not Call/"` and commit with message `feat: add Do Not Call agent project`
3. Push to `Do Not Call` branch: `git push origin "Do Not Call"`

**Status:** [x] done

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Project location | GSAHackathon subfolder | User confirmed — same repo, new branch |
| FTC registration | URL + instructions, no write API | FTC has no public machine-writable DNC registration API |
| Letter delivery | Email body only (user copy-pastes) | User confirmed email; no SMTP integration needed |
| Phone lookup | Numverify API (optional) | Free tier; gracefully skips if no `NUMVERIFY_API_KEY` |
| Violation logic | Deterministic logic tree in `assess_violation` | Keeps LLM out of legal determinations; no hallucinated citations |
| Session tracking | Functional accumulator — list passed in context | User confirmed track within session; no DB needed |
| Escalation | Automatic at `violation_count >= 2` | User confirmed — agent offers remedies without being asked |
| Statutory damages | Read from `config/dnc_config.json` | Tunable without code changes |

## Disclaimer

All tools and agent outputs carry the disclaimer: *"This tool provides general information
only and is not legal advice. Consult a licensed attorney for guidance specific to your
situation. TCPA enforcement outcomes depend on facts and jurisdiction."*
