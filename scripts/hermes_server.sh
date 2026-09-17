#!/usr/bin/env bash
# Start / stop / check the codec-agent Hermes Agent API server.
#
# Hermes profile `codecagent` (~/.hermes/profiles/codecagent), api_server platform on
# 127.0.0.1:8659, model name `codec-agent`, tools disabled for that platform. The
# upstream LLM is `model.default` in the profile's config.yaml.
# Client settings (HERMES_BASE_URL / HERMES_API_KEY / HERMES_MODEL) live in ./.env.
set -euo pipefail
PROFILE=codecagent
HOME_DIR="$HOME/.hermes/profiles/$PROFILE"
VENV="$HOME/.hermes/hermes-agent/venv"
PORT=8659

case "${1:-status}" in
  start)
    cd "$HOME_DIR"
    VIRTUAL_ENV="$VENV" HERMES_HOME="$HOME_DIR" nohup "$VENV/bin/python" -m hermes_cli.main \
      --profile "$PROFILE" gateway run --replace > "$HOME_DIR/logs/codec-api-server.out" 2>&1 &
    disown
    for _ in $(seq 1 60); do
      curl -sf -m 2 "http://127.0.0.1:$PORT/health" >/dev/null && { echo "up on :$PORT"; exit 0; }
      sleep 1
    done
    echo "did not come up; see $HOME_DIR/logs/codec-api-server.out" >&2; exit 1 ;;
  stop)
    pkill -f "profile $PROFILE gateway run" && echo stopped || echo "not running" ;;
  status)
    curl -s -m 2 "http://127.0.0.1:$PORT/health" || echo "down"
    echo
    grep -A3 '^model:' "$HOME_DIR/config.yaml" ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
