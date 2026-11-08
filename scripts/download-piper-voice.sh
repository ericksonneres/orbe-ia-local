#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VOICE_DIR="${PROJECT_DIR}/whisper/models/tts"
REVISION="5041a7a49ae5b3a0f30e6837769eb03fdbb231bd"
BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/${REVISION}"

mkdir -p "$VOICE_DIR"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_DIR"' EXIT

download_verified() {
    local relative_path="$1"
    local filename="$2"
    local sha256="$3"
    local destination="${VOICE_DIR}/${filename}"
    local temporary="${TEMP_DIR}/${filename}"

    if [[ -f "$destination" ]] \
        && printf '%s  %s\n' "$sha256" "$destination" | sha256sum --check --status; then
        printf 'Já verificado: %s\n' "$destination"
        return
    fi

    curl --fail --location --retry 3 --silent --show-error \
        --output "$temporary" "${BASE_URL}/${relative_path}"
    printf '%s  %s\n' "$sha256" "$temporary" | sha256sum --check
    mv -- "$temporary" "$destination"
    printf 'Instalado: %s\n' "$destination"
}

install_voice() {
    local relative_path="$1"
    local filename="$2"
    local model_sha256="$3"
    local config_sha256="$4"

    download_verified "${relative_path}/${filename}" \
        "$filename" "$model_sha256"
    download_verified "${relative_path}/${filename}.json" \
        "${filename}.json" "$config_sha256"
}

install_voice \
    "pt/pt_BR/faber/medium" "pt_BR-faber-medium.onnx" \
    "858555e3a064209c57088fe6bd70c4c3dc54d03eaa00c45d5ecaf43a33f95aa7" \
    "7e694de195ae3fc36dd732c445eb04fb49b649854893cb5506b978f0d50a1d6f"
install_voice \
    "pt/pt_BR/cadu/medium" "pt_BR-cadu-medium.onnx" \
    "765f0809a6ea9035d4a6d0d008dbf8876e68b2dd32029312672fa8f405bdb535" \
    "5fe03aa3d4901880554905b12075713cd552598c8a350455a1ec73f8b4e6be19"
install_voice \
    "pt/pt_BR/jeff/medium" "pt_BR-jeff-medium.onnx" \
    "3a6f4c46355813c2b7bbc4d16b6d13d60ed72074b952a393baace82a7d0c94b5" \
    "7bf8145b572b36806f5ce0f1d3322b6711975bc7d0473e8d36fced4a9ec0030d"
install_voice \
    "pt/pt_BR/edresson/low" "pt_BR-edresson-low.onnx" \
    "de4cecee38b30bb1a6378a337af605d59f0c377df702c6a6752870db8991cd84" \
    "f138992d2e777d1e3aa0bbb14c2d324307b0f342c1bcf20978765b3bea506c56"
install_voice \
    "en/en_US/hfc_female/medium" "en_US-hfc_female-medium.onnx" \
    "914c473788fc1fa8b63ace1cdcdb44588f4ae523d3ab37df1536616835a140b7" \
    "03f1fa0622b80463283592d97aca9f6e89aec345a5c56b7257723e0093c58b6c"
install_voice \
    "en/en_US/hfc_male/medium" "en_US-hfc_male-medium.onnx" \
    "d11e403a02bdf5a670c877b3dc56e0e1c8cece6fb30289586314dffdc0a78cb0" \
    "f66847424aed0bf99ecbb5d7cfde47c0a906f426a0daf7c46f305e7d21afd886"
install_voice \
    "es/es_ES/davefx/medium" "es_ES-davefx-medium.onnx" \
    "6658b03b1a6c316ee4c265a9896abc1393353c2d9e1bca7d66c2c442e222a917" \
    "0e0dda87c732f6f38771ff274a6380d9252f327dca77aa2963d5fbdf9ec54842"
install_voice \
    "es/es_AR/daniela/high" "es_AR-daniela-high.onnx" \
    "7ceb1fc0dab349418c5b54a639ae9ee595212d7c9ea422220d8419163d5cc985" \
    "aedbf69647e1d754c62ecf8e0366ca5f16af3e768e3c6b5329af6eb6bde3852b"
