#!/bin/bash
# feishu-poll.sh — Poll Feishu messages and inject into Hermes gateway
#
# Usage: ./scripts/feishu-poll.sh [chat_id] [interval_seconds]
#
# Requires: lark-cli configured with auth, curl
# Token cost: zero (just HTTP polling). LLM only triggers on new messages.

set -euo pipefail

CHAT_ID="${1:?Usage: feishu-poll.sh <chat_id> [interval_seconds]}"
INTERVAL="${2:-5}"
GATEWAY_URL="http://127.0.0.1:9119"
LAST_MSG_ID_FILE="/tmp/feishu-last-msg-${CHAT_ID//\//-}"

# Initialize last seen message
if [ ! -f "$LAST_MSG_ID_FILE" ]; then
  echo "" > "$LAST_MSG_ID_FILE"
fi

echo "[feishu-poll] Watching chat $CHAT_ID every ${INTERVAL}s"
echo "[feishu-poll] Gateway: $GATEWAY_URL"

while true; do
  # Fetch latest messages
  RESPONSE=$(lark-cli im +chat-messages-list \
    --chat-id "$CHAT_ID" \
    --page-size 5 \
    --order asc \
    --format json \
    --as bot \
    2>/dev/null || echo '{"items":[]}')

  # Extract messages
  MSGS=$(echo "$RESPONSE" | jq -r '.items[]? | "\(.message_id)\t\(.sender.sender_type)\t\(.body.content // "")"' 2>/dev/null || true)

  LAST_SEEN=$(cat "$LAST_MSG_ID_FILE")

  while IFS=$'\t' read -r MSG_ID SENDER_TYPE CONTENT; do
    [ -z "$MSG_ID" ] && continue

    # Skip if already seen
    if [ "$MSG_ID" = "$LAST_SEEN" ]; then
      continue
    fi

    # Skip bot messages (avoid echo loops)
    if [ "$SENDER_TYPE" = "app" ]; then
      LAST_SEEN="$MSG_ID"
      continue
    fi

    # Extract text content
    TEXT=$(echo "$CONTENT" | jq -r '.text // empty' 2>/dev/null || echo "$CONTENT")

    if [ -n "$TEXT" ] && [ "$TEXT" != "null" ]; then
      echo "[feishu-poll] New message from $SENDER_TYPE: ${TEXT:0:100}"
      # Inject into hermes gateway
      curl -s -X POST "$GATEWAY_URL/api/webhook" \
        -H "Content-Type: application/json" \
        -d "{\"text\": \"$TEXT\", \"source\": \"feishu\", \"chat_id\": \"$CHAT_ID\"}" \
        >/dev/null 2>&1 || true
    fi

    LAST_SEEN="$MSG_ID"
  done <<< "$MSGS"

  echo "$LAST_SEEN" > "$LAST_MSG_ID_FILE"
  sleep "$INTERVAL"
done
