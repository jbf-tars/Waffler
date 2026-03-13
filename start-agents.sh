#!/bin/zsh
# Start Paperclip agents on TARS Mac
# Each agent runs as a background process with its own API key

WAFFLER_DIR="$(cd "$(dirname "$0")" && pwd)"
API_BASE="http://100.97.68.6:3100"
COMPANY_ID="3efc6ba7-1261-4664-99b2-18ac9323e78e"

# Source agent keys
source "$WAFFLER_DIR/.paperclip-agents.env"

echo "Waffler Paperclip Agents — TARS Mac"
echo "===================================="
echo "API Base: $API_BASE"
echo "Company: $COMPANY_ID"
echo "Workspace: $WAFFLER_DIR"
echo ""

# Verify connectivity
echo "Verifying API connectivity..."
for name_var in "Mac Dev:PAPERCLIP_MACDEV_KEY" "QA:PAPERCLIP_QA_KEY" "Release:PAPERCLIP_RELEASE_KEY" "Web Dev:PAPERCLIP_WEBDEV_KEY"; do
  name="${name_var%%:*}"
  var="${name_var#*:}"
  key="${(P)var}"
  code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $key" "$API_BASE/api/companies/$COMPANY_ID/agents")
  if [ "$code" = "200" ]; then
    echo "  ✅ $name — connected"
  else
    echo "  ❌ $name — $code"
  fi
done

echo ""
echo "Agent keys are verified. Use these env vars when spawning sub-agents:"
echo ""
echo "  PAPERCLIP_API_URL=$API_BASE"
echo "  PAPERCLIP_COMPANY_ID=$COMPANY_ID"
echo "  PAPERCLIP_API_KEY=<agent-specific-key>"
echo ""
echo "Done."
