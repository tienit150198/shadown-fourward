#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf 'SHADOW: %s\n' "$*" >&2
    exit 1
}

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ ! -e .env && ! -L .env ]]; then
    (umask 077; set -o noclobber; cat < .env.example > .env) || fail 'Could not create .env.'
    printf 'Created .env from .env.example.\n'
fi
[[ -f .env && -r .env ]] || fail 'The repository .env must be a readable file.'

docker compose version >/dev/null 2>&1 || fail 'Install Docker Engine with the Docker Compose plugin.'

# Compose injects .env into the app. Ignore exported overrides for the values
# checked here so preflight, container settings and the health URL agree.
setting_names=(
    LLM_BASE_URL LLM_API_KEY VISION_MODEL COMPILER_MODEL SHADOW_MODE
    SHADOW_INTENT_PROVIDER OPENROUTER_API_KEY TRYON_PHOTO_PROVIDER QWEN_IMAGE_BASE_URL
    VIDU_API_KEY VIDU_API_BASE_URL
    SHADOW_DOMAIN SHADOW_PORT SHADOW_ACCESS_CODE
)
unset "${setting_names[@]}"
compose=(docker compose --env-file .env -f compose.yaml)
config=$("${compose[@]}" config --environment 2>/dev/null) || fail 'Could not read Compose configuration. Check the .env syntax.'
declare -A settings=()
while IFS= read -r entry; do
    case "${entry%%=*}" in
        LLM_BASE_URL|LLM_API_KEY|VISION_MODEL|COMPILER_MODEL|SHADOW_MODE|\
        SHADOW_INTENT_PROVIDER|OPENROUTER_API_KEY|TRYON_PHOTO_PROVIDER|QWEN_IMAGE_BASE_URL|\
        VIDU_API_KEY|VIDU_API_BASE_URL|SHADOW_DOMAIN|SHADOW_PORT|SHADOW_ACCESS_CODE)
            settings["${entry%%=*}"]=${entry#*=}
            ;;
    esac
done <<< "$config"
unset config entry

missing=()
for key in LLM_BASE_URL LLM_API_KEY VISION_MODEL COMPILER_MODEL SHADOW_INTENT_PROVIDER OPENROUTER_API_KEY TRYON_PHOTO_PROVIDER VIDU_API_KEY; do
    if [[ ! ${settings[$key]:-} =~ [^[:space:]] ]]; then
        missing+=("$key")
    fi
done
domain=${settings[SHADOW_DOMAIN]:-}
if [[ -n $domain && ! ${settings[SHADOW_ACCESS_CODE]:-} =~ [^[:space:]] ]]; then
    missing+=(SHADOW_ACCESS_CODE)
fi
if ((${#missing[@]})); then
    fail "Set these values in .env and run again: ${missing[*]}"
fi

[[ ${settings[LLM_BASE_URL]%/} == https://llm.hypertek.vn/v1 ]] || fail 'Set LLM_BASE_URL=https://llm.hypertek.vn/v1 in .env.'
mode=${settings[SHADOW_MODE]:-auto}
[[ $mode == ai || $mode == auto ]] || fail 'Set SHADOW_MODE=ai or SHADOW_MODE=auto in .env.'
[[ ${settings[SHADOW_INTENT_PROVIDER]} == jev ]] || fail 'Set SHADOW_INTENT_PROVIDER=jev in .env.'
[[ ${settings[TRYON_PHOTO_PROVIDER]} == vidu ]] || fail 'Set TRYON_PHOTO_PROVIDER=vidu in .env.'
image_url=${settings[QWEN_IMAGE_BASE_URL]:-https://llm.hypertek.vn/v1}
[[ ${image_url%/} == https://llm.hypertek.vn/v1 ]] || fail 'Set QWEN_IMAGE_BASE_URL to the Hypertek endpoint or leave it empty.'
vidu_url=${settings[VIDU_API_BASE_URL]:-https://api.vidu.com}
case "${vidu_url%/}" in
    https://api.vidu.com|https://api.vidu.cn) ;;
    *) fail 'Set VIDU_API_BASE_URL to https://api.vidu.com or https://api.vidu.cn in .env.' ;;
esac
port=${settings[SHADOW_PORT]:-8000}
unset settings

[[ $port =~ ^[1-9][0-9]{0,4}$ ]] && ((port <= 65535)) || fail 'Set SHADOW_PORT to a TCP port between 1 and 65535.'
if [[ -n $domain ]]; then
    [[ $domain =~ ^[[:alnum:]]([[:alnum:]-]*[[:alnum:]])?(\.[[:alnum:]]([[:alnum:]-]*[[:alnum:]])?)+$ ]] || fail 'Set SHADOW_DOMAIN to a DNS hostname such as shadow.example.com.'
    compose+=(-f compose.https.yaml)
fi

command -v curl >/dev/null || fail 'Install curl to check the deployed app.'
docker info >/dev/null 2>&1 || fail 'Cannot reach Docker Engine. Start it and check your Docker permissions.'
"${compose[@]}" config --quiet 2>/dev/null || fail 'Invalid Compose configuration. Check .env and the selected Compose files.'
"${compose[@]}" up -d --build --wait --wait-timeout 150 --remove-orphans

url="http://127.0.0.1:$port"
ai_health_pattern='"mode"[[:space:]]*:[[:space:]]*"ai"'
health=$(curl -fsS --noproxy 127.0.0.1 --max-time 10 "$url/api/health") || fail 'The app did not answer on the host port. Check Docker Compose service status.'
[[ $health =~ $ai_health_pattern ]] || fail 'The app answered but did not report AI mode.'
if [[ -n $domain ]]; then
    url="https://$domain"
    if ! health=$(curl -fs --retry 12 --retry-delay 5 --retry-max-time 65 --retry-all-errors --max-time 8 "$url/api/health") ||
        [[ ! $health =~ $ai_health_pattern ]]; then
        fail 'The app is running, but the HTTPS health check failed. Check DNS, ports 80 and 443, and the Caddy logs.'
    fi
fi
printf 'SHADOW is ready at %s\n' "$url"
