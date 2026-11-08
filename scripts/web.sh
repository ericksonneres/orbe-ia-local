#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_SH="${HOME}/miniconda3/etc/profile.d/conda.sh"

if ! command -v conda >/dev/null 2>&1; then
    if [[ ! -r "$CONDA_SH" ]]; then
        printf 'Conda não encontrado. Verifique a instalação em %s.\n' "$CONDA_SH" >&2
        exit 127
    fi
    # shellcheck disable=SC1090
    source "$CONDA_SH"
fi

if ! conda env list | awk '$1 == "ai-agents" { found = 1 } END { exit !found }'; then
    printf 'O ambiente Conda "ai-agents" não existe. Crie-o com:\n' >&2
    printf '  conda env create -f "%s/agents/environment.yml"\n' "$PROJECT_DIR" >&2
    exit 1
fi

cd -- "$PROJECT_DIR"
exec conda run --no-capture-output -n ai-agents python -m agents.server "$@"
