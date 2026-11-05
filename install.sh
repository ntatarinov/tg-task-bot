#!/bin/sh
# One-command server install. Run from the unpacked archive:
#
#   sh install.sh          the bot and its model on this server
#   sh install.sh cloud    the bot only; the model is the cloud service set in .env
#
set -eu
cd "$(dirname "$0")"

mode="${1:-local}"
default_model="gemma4:26b"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created the .env file. Fill in BOT_TOKEN, ALLOWED_CHAT_IDS, ADMIN_USER_IDS"
    echo "and run the installer again."
    exit 1
fi

# A .env saved on Windows has CRLF line ends, which would end up inside the values.
sed -i 's/\r$//' .env

# Sets a value only if .env has none, so settings made earlier are kept.
set_default() {
    if ! grep -q "^$1=." .env; then
        sed -i "/^$1=/d" .env
        printf '%s=%s\n' "$1" "$2" >>.env
    fi
}

chmod 600 .env

if ! grep -q '^ADMIN_PANEL_PASSWORD=.' .env; then
    password="$(tr -dc 'a-z0-9' </dev/urandom | head -c 24)"
    set_default ADMIN_PANEL_PASSWORD "$password"
    echo "Created a password for the control panel: $password"
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi

mkdir -p data

if [ "$mode" = "cloud" ]; then
    files="-f docker-compose.cloud.yml"
else
    # On this computer Ollama was on localhost; here it is the `ollama` container.
    sed -i 's#^OPENAI_BASE_URL=http://\(localhost\|127\.0\.0\.1\):11434#OPENAI_BASE_URL=http://ollama:11434#' .env
    if ! grep -q '^OPENAI_BASE_URL=.' .env; then
        # A first install: run the local model. Later installs keep the stack chosen in the panel.
        sed -i '/^OPENAI_MODEL=/d; /^LLM_MAX_OUTPUT_TOKENS=/d; /^LLM_TIMEOUT_SECONDS=/d' .env
        set_default OPENAI_BASE_URL http://ollama:11434/v1
        set_default OPENAI_API_KEY ollama
        set_default LLM_PROVIDER openai
        set_default OPENAI_MODEL "$default_model"
        set_default LLM_REASONING_EFFORT none
        set_default LLM_TIMEOUT_SECONDS 900
        set_default LLM_MAX_OUTPUT_TOKENS 6000
    fi

    files="-f docker-compose.selfhost.yml"
    if docker info 2>/dev/null | grep -qi nvidia; then
        echo "Found an NVIDIA GPU, the model will run on it."
        files="$files -f docker-compose.gpu.yml"
    fi
    echo "The first start downloads the model, this can take several minutes."
fi

# shellcheck disable=SC2086
docker compose $files up -d --build
echo
# shellcheck disable=SC2086
docker compose $files exec -T bot python -m app.doctor || true
echo
echo "Bot logs:      docker compose $files logs -f bot"
echo "Control panel: ssh -L 8090:127.0.0.1:8090 <user>@<server>, then http://localhost:8090"
