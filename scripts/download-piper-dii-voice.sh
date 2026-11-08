#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VOICE_DIR="${PROJECT_DIR}/whisper/models/tts"
REVISION="38281189bdbc6a0346041925b56da6d71d6fba3d"
BASE_URL="https://huggingface.co/OpenVoiceOS/pipertts_pt-BR_dii/resolve/${REVISION}"

printf '%s\n' \
    "Aviso: Dii usa licença CC BY-NC-ND 4.0: uso não comercial com atribuição;" \
    "não modifique o modelo nem crie vozes derivadas." >&2

mkdir -p "$VOICE_DIR"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_DIR"' EXIT

download_verified() {
    local filename="$1"
    local sha256="$2"
    local destination="${VOICE_DIR}/${filename}"
    local temporary="${TEMP_DIR}/${filename}"

    if [[ -f "$destination" ]] \
        && printf '%s  %s\n' "$sha256" "$destination" | sha256sum --check --status; then
        printf 'Já verificado: %s\n' "$destination"
        return
    fi

    curl --fail --location --retry 3 --silent --show-error \
        --output "$temporary" "${BASE_URL}/${filename}"
    printf '%s  %s\n' "$sha256" "$temporary" | sha256sum --check
    mv -- "$temporary" "$destination"
    printf 'Instalado: %s\n' "$destination"
}

download_verified \
    "dii_pt-BR.onnx" \
    "fb15a447ba7482b922e1d2fefea71c1031f760e72be24cce7794e26fe11d086b"
download_verified \
    "dii_pt-BR.onnx.json" \
    "306998295ac96a449d43a19c8aad65b31e040341e15decbdf4aa68d88670ec84"
