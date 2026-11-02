# Do Not Call Agent 🚫📞

An IBM watsonx Orchestrate agent that helps users register on the FTC Do Not Call Registry, identify TCPA violations, generate cease-and-desist emails, and pursue legal remedies against repeat callers — all within a conversational interface.

---

> **⚠️ Legal Disclaimer:** This project provides general information only and is **not legal advice**. Consult a licensed attorney for guidance specific to your situation.

---

## Features

- Register your phone number on the FTC National Do Not Call Registry
- Look up incoming caller information (carrier, line type) via Numverify
- Assess whether a call violates TCPA / Do Not Call rules with a deterministic logic tree
- Track multiple violations within a session with automatic escalation
- Generate cease-and-desist or TCPA violation notice emails (copy-paste ready)
- Get step-by-step FTC/FCC complaint guidance and TCPA small claims court instructions

---

## Project Structure

```
Do Not Call/
├── agents/
│   └── do_not_call_agent.yaml       ← watsonx Orchestrate native agent definition
├── tools/
│   ├── dnc_registration.py          ← register_number, check_registration_status
│   ├── dnc_violation_check.py       ← lookup_caller, assess_violation
│   ├── dnc_letter_generator.py      ← generate_violation_letter
│   ├── dnc_session_tracker.py       ← track_violation (session accumulator)
│   └── dnc_legal_guidance.py        ← get_legal_remedies
├── config/
│   └── dnc_config.json              ← API base URLs, statutory amounts, email format
├── templates/
│   ├── cease_and_desist_email.txt   ← Email body template (first violation)
│   ├── escalation_email.txt         ← Email body template (repeat/willful violation)
│   └── ftc_complaint_steps.txt      ← Complaint steps template
└── README.md
```

---

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `IBM_CLOUD_API_KEY` | Yes | watsonx Orchestrate authentication |
| `NUMVERIFY_API_KEY` | No | Live phone carrier lookup (gracefully skips if absent — get a free key at [numverify.com](https://numverify.com)) |

---

## Setup and Usage

### 1. Install dependencies

From the `GSAHackathon` repo root:

```bash
pip install -r requirements.txt
```

### 2. Activate your watsonx Orchestrate environment

```bash
orchestrate env activate
```

### 3. Import the agent

```bash
orchestrate agents import "Do Not Call/agents/do_not_call_agent.yaml"
```

### 4. Import tools

Import each tool file individually:

```bash
orchestrate tools import "Do Not Call/tools/dnc_registration.py"
orchestrate tools import "Do Not Call/tools/dnc_violation_check.py"
orchestrate tools import "Do Not Call/tools/dnc_letter_generator.py"
orchestrate tools import "Do Not Call/tools/dnc_session_tracker.py"
orchestrate tools import "Do Not Call/tools/dnc_legal_guidance.py"
```

### 5. Start chatting

Open the **Do Not Call Agent** in the watsonx Orchestrate chat interface and use one of the starter prompts, or type your question directly.

---

## Example Conversations

### Registration Flow

> **User:** I want to register my number on the Do Not Call list.
>
> **Agent:** I can help with that. What is your name and the phone number you'd like to register?
>
> **User:** Jane Doe, 555-867-5309
>
> **Agent:** To register (555) 867-5309 on the FTC National Do Not Call Registry, visit:
> https://www.donotcall.gov/register.html
>
> Enter your phone number and email address to complete registration. Registration is free and typically takes up to 31 days to become effective. Once registered, most telemarketers are legally required to stop calling you (47 U.S.C. § 227(c)).

---

### Violation Check Flow

> **User:** I got a robocall this morning from 800-555-0199 trying to sell me an extended car warranty.
>
> **Agent:** Let me look up that number and assess whether this is a violation. A few quick questions: Is your number registered on the Do Not Call Registry, and has it been registered for more than 31 days?
>
> **User:** Yes, registered for over a year.
>
> **Agent:** Did you ever give this company (or any company acting on their behalf) written or verbal consent to contact you?
>
> **User:** Never.
>
> **Agent:** ✅ **Verdict: VIOLATION**
> This call likely violates the FTC Do Not Call rules and the Telephone Consumer Protection Act (TCPA), 47 U.S.C. § 227(c). You registered more than 31 days ago, gave no consent, and no recognized exemption applies. Would you like me to generate a cease-and-desist email to send to the caller?

---

### Letter Generation Flow

> **User:** Yes, generate the email for me.
>
> **Agent:** I need a few more details. What is your email address, and what date and time did the call occur?
>
> **User:** jane@example.com, today at 9:15 AM.
>
> **Agent:** Here is your cease-and-desist email — copy and paste it into your email client:
>
> **Subject:** Cease and Desist — Do Not Call Violation
>
> Dear Sir or Madam,
>
> My name is Jane Doe and my phone number is (555) 867-5309 ...
> *(full email body with statutory citations)*
>
> *Disclaimer: This letter is provided for informational purposes only and is not legal advice.*

---

## Legal References

- **47 U.S.C. § 227** — Telephone Consumer Protection Act (TCPA)
- **16 C.F.R. Part 310** — FTC Telemarketing Sales Rule
- FTC Do Not Call Registry: https://www.donotcall.gov
- FTC Complaint Portal: https://reportfraud.ftc.gov
- FCC Complaint Portal: https://consumercomplaints.fcc.gov/hc/en-us

---

*This project was built for the GSA Hackathon using IBM watsonx Orchestrate.*
