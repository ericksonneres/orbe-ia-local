from __future__ import annotations

import argparse
import sys

import requests

from agents.core import AgentCore
from agents.port_forwarder import LocalPortForwarder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chat local com roteamento entre perfis de texto e código."
    )
    parser.add_argument("--task", choices=("text", "code"), default="text")
    parser.add_argument("--model", help="Modelo Ollama instalado; vazio usa o perfil.")
    parser.add_argument("--message", help="Envia uma única mensagem e encerra.")
    media = parser.add_mutually_exclusive_group()
    media.add_argument(
        "--image",
        action="store_true",
        help="Gera uma imagem para cada resposta usando o ComfyUI local.",
    )
    media.add_argument(
        "--video",
        action="store_true",
        help="Gera um vídeo curto com Wan2.1 local.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    forwarder = LocalPortForwarder()
    try:
        forwarder.start()
        core = AgentCore()
        messages: list[dict[str, str]] = []
        if args.message:
            messages.append({"role": "user", "content": args.message})
            result = core.chat(
                messages,
                task=args.task,
                model=args.model,
                generate_image=args.image,
                generate_video=args.video,
            )
            print(result["answer"])
            print(f"\nModelo: {result['model']} ({result['task']})")
            if result["image_url"]:
                print(f"Imagem: {result['image_url']}")
            if result.get("image_error"):
                print(f"Erro na geração da imagem: {result['image_error']}", file=sys.stderr)
            return 0

        print("Chat local. Digite /sair para encerrar.")
        while True:
            prompt = input("Você: ").strip()
            if prompt.lower() in {"/sair", "/exit", "/quit"}:
                return 0
            if not prompt:
                continue
            messages.append({"role": "user", "content": prompt})
            result = core.chat(
                messages,
                task=args.task,
                model=args.model,
                generate_image=args.image,
                generate_video=args.video,
            )
            answer = str(result["answer"])
            messages.append({"role": "assistant", "content": answer})
            print(f"Agente ({result['model']}): {answer}\n")
            if result["image_url"]:
                print(f"Imagem: {result['image_url']}\n")
            if result.get("image_error"):
                print(f"Erro na geração da imagem: {result['image_error']}", file=sys.stderr)
    except (requests.RequestException, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"Erro no chat local: {exc}", file=sys.stderr)
        return 1
    finally:
        forwarder.close()


if __name__ == "__main__":
    raise SystemExit(main())
