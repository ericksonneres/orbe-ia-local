#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VOICE_DIR="${PROJECT_DIR}/whisper/models/tts"
BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"

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
    "kokoro-v1.0.onnx" \
    "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"
download_verified \
    "voices-v1.0.bin" \
    "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
