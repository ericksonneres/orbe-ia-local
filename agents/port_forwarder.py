from __future__ import annotations

import ipaddress
import json
import logging
import select
import socket
import socketserver
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

LOGGER = logging.getLogger("agents.port_forwarder")


@dataclass(frozen=True)
class ServicePort:
    container_name: str
    local_port: int
    container_port: int


DEFAULT_SERVICE_PORTS = (
    ServicePort("ollama", 11434, 11434),
    ServicePort("whisper", 8001, 8000),
    ServicePort("stable-diffusion", 1111, 1111),
    ServicePort("stable-diffusion", 8188, 8188),
    ServicePort("stable-diffusion", 18188, 8188),
)


class _ContainerTarget:
    def __init__(
        self,
        container_name: str,
        target_resolver: Callable[[str], str],
    ) -> None:
        self.container_name = container_name
        self._target_resolver = target_resolver
        self._address: str | None = None
        self._resolved_at = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            if self._address is None or time.monotonic() - self._resolved_at >= 10:
                self._address = self._target_resolver(self.container_name)
                self._resolved_at = time.monotonic()
            return self._address


class _ForwardingHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            target_host, target_port = self.server.resolve_target()
        except (OSError, RuntimeError) as exc:
            LOGGER.warning("Não foi possível resolver o destino do encaminhamento: %s", exc)
            return
        try:
            target = socket.create_connection((target_host, target_port), timeout=5)
        except OSError as exc:
            LOGGER.warning(
                "Não foi possível encaminhar a porta local %s para %s:%s: %s",
                self.server.server_address[1],
                target_host,
                target_port,
                exc,
            )
            return

        with target:
            sockets = [self.request, target]
            while sockets:
                try:
                    readable, _, _ = select.select(sockets, [], [], 30)
                    for source in readable:
                        data = source.recv(65536)
                        destination = target if source is self.request else self.request
                        if data:
                            destination.sendall(data)
                        else:
                            sockets.remove(source)
                            try:
                                destination.shutdown(socket.SHUT_WR)
                            except OSError:
                                pass
                except OSError as exc:
                    LOGGER.debug("Conexão de encaminhamento encerrada: %s", exc)
                    return


class _ForwardingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        target_resolver: Callable[[], tuple[str, int]],
    ) -> None:
        self.resolve_target = target_resolver
        super().__init__(address, _ForwardingHandler)


class LocalPortForwarder:
    def __init__(
        self,
        service_ports: tuple[ServicePort, ...] = DEFAULT_SERVICE_PORTS,
        target_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self._service_ports = service_ports
        self._target_resolver = target_resolver or _resolve_internal_container_ip
        self._servers: list[_ForwardingServer] = []
        self._threads: list[threading.Thread] = []

    @property
    def local_ports(self) -> tuple[int, ...]:
        return tuple(server.server_address[1] for server in self._servers)

    def start(self) -> None:
        targets: dict[str, _ContainerTarget | None] = {}
        try:
            for service_port in self._service_ports:
                if _local_port_is_open(service_port.local_port):
                    continue
                if service_port.container_name not in targets:
                    target = _ContainerTarget(
                        service_port.container_name, self._target_resolver
                    )
                    try:
                        target()
                    except RuntimeError as exc:
                        LOGGER.warning(
                            "Encaminhamento indisponível para o container %s: %s",
                            service_port.container_name,
                            exc,
                        )
                        targets[service_port.container_name] = None
                    else:
                        targets[service_port.container_name] = target
                target = targets[service_port.container_name]
                if target is None:
                    continue
                resolver = lambda target=target, port=service_port.container_port: (
                    target(),
                    port,
                )
                server = _ForwardingServer(
                    ("127.0.0.1", service_port.local_port),
                    resolver,
                )
                thread = threading.Thread(
                    target=server.serve_forever,
                    name=f"local-port-{service_port.local_port}",
                    daemon=True,
                )
                thread.start()
                self._servers.append(server)
                self._threads.append(thread)
                LOGGER.info(
                    "Encaminhando 127.0.0.1:%s para o container %s:%s.",
                    server.server_address[1],
                    service_port.container_name,
                    service_port.container_port,
                )
        except (OSError, RuntimeError) as exc:
            self.close()
            raise RuntimeError(
                f"Não foi possível iniciar o encaminhamento local: {exc}"
            ) from exc

    def close(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=2)
        self._servers.clear()
        self._threads.clear()

    def __enter__(self) -> LocalPortForwarder:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _local_port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.15):
            return True
    except OSError:
        return False


def _resolve_internal_container_ip(container_name: str) -> str:
    try:
        container_result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                container_name,
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"Não foi possível consultar o container {container_name!r}: {exc}"
        ) from exc
    if container_result.returncode != 0:
        detail = container_result.stderr.strip() or "container indisponível"
        raise RuntimeError(f"Não foi possível consultar {container_name!r}: {detail}")
    try:
        networks = json.loads(container_result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"O Docker retornou redes inválidas para {container_name!r}."
        ) from exc
    if not isinstance(networks, dict):
        raise RuntimeError(f"O Docker não informou redes para {container_name!r}.")

    internal_addresses: list[str] = []
    for network_name, details in networks.items():
        if not isinstance(network_name, str) or not isinstance(details, dict):
            continue
        address = details.get("IPAddress")
        if not isinstance(address, str) or not address:
            continue
        try:
            network_result = subprocess.run(
                [
                    "docker",
                    "network",
                    "inspect",
                    "--format",
                    "{{.Internal}}",
                    network_name,
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                f"Não foi possível consultar a rede Docker {network_name!r}: {exc}"
            ) from exc
        if network_result.returncode != 0:
            detail = network_result.stderr.strip() or "rede indisponível"
            raise RuntimeError(f"Não foi possível consultar a rede: {detail}")
        if network_result.stdout.strip().lower() == "true":
            internal_addresses.append(address)

    if len(internal_addresses) != 1:
        raise RuntimeError(
            f"Esperava uma rede interna para {container_name!r}; "
            f"encontrei {len(internal_addresses)}."
        )
    try:
        address = ipaddress.ip_address(internal_addresses[0])
    except ValueError as exc:
        raise RuntimeError(
            f"O Docker retornou um endereço IP inválido para {container_name!r}."
        ) from exc
    if address.version != 4 or not address.is_private or address.is_loopback:
        raise RuntimeError(
            f"O Docker retornou um endereço não local para {container_name!r}."
        )
    return str(address)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    forwarder = LocalPortForwarder()
    try:
        forwarder.start()
    except RuntimeError as exc:
        LOGGER.error("%s", exc)
        return 1
    if not forwarder.local_ports:
        LOGGER.warning(
            "Nenhum encaminhamento próprio foi iniciado; portas já ativas ou "
            "containers indisponíveis."
        )
    else:
        LOGGER.info("Encaminhamento ativo. Pressione Ctrl+C para encerrar.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        forwarder.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
