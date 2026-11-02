#!/usr/bin/env bash
# import-all.sh — imports all Do Not Call tools and agent into the active wxO environment.
# Run from the GSAHackathon project root after activating the venv:
#   source venv/bin/activate
#   orchestrate env activate <your-env> --api-key "$IBM_CLOUD_API_KEY"
#   bash "Do Not Call/scripts/import-all.sh"
set -e

echo "Importing Do Not Call tools..."
orchestrate tools import -k python -f "Do Not Call/tools/dnc_registration.py"
orchestrate tools import -k python -f "Do Not Call/tools/dnc_violation_check.py"
orchestrate tools import -k python -f "Do Not Call/tools/dnc_session_tracker.py"
orchestrate tools import -k python -f "Do Not Call/tools/dnc_letter_generator.py"
orchestrate tools import -k python -f "Do Not Call/tools/dnc_legal_guidance.py"

echo "Importing Do Not Call agent..."
orchestrate agents import -f "Do Not Call/agents/do_not_call_agent.yaml"

echo "✅ All Do Not Call tools and agent imported successfully."
echo ""
echo "To test, run:"
echo "  orchestrate chat ask -n do_not_call_agent \"I want to register my phone number on the Do Not Call list.\" -r"
