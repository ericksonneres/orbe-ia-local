from __future__ import annotations

import json
import socket
import socketserver
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.port_forwarder import (
    LocalPortForwarder,
    ServicePort,
    _resolve_internal_container_ip,
)


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while data := self.request.recv(4096):
            self.request.sendall(data)


class PortForwarderTests(unittest.TestCase):
    def test_forwards_tcp_traffic_only_on_loopback(self) -> None:
        target = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), EchoHandler
        )
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()
        self.addCleanup(target.server_close)
        self.addCleanup(target_thread.join, 2)
        self.addCleanup(target.shutdown)

        forwarder = LocalPortForwarder(
            service_ports=(
                ServicePort("test-container", 0, target.server_address[1]),
            ),
            target_resolver=Mock(return_value="127.0.0.1"),
        )
        with forwarder:
            local_port = forwarder.local_ports[0]
            with socket.create_connection(("127.0.0.1", local_port), timeout=2) as client:
                client.sendall(b"local proxy")
                self.assertEqual(client.recv(11), b"local proxy")

        self.assertNotEqual(local_port, 0)
        self.assertFalse(forwarder.local_ports)

    def test_reuses_an_already_reachable_local_service(self) -> None:
        resolver = Mock(return_value="127.0.0.1")
        forwarder = LocalPortForwarder(
            service_ports=(ServicePort("ollama", 11434, 11434),),
            target_resolver=resolver,
        )
        with patch("agents.port_forwarder._local_port_is_open", return_value=True):
            forwarder.start()
        try:
            self.assertEqual(forwarder.local_ports, ())
            resolver.assert_not_called()
        finally:
            forwarder.close()

    def test_resolves_only_private_addresses_on_internal_networks(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            self.assertEqual(
                set(_kwargs),
                {"capture_output", "check", "text", "timeout"},
            )
            if command[1] == "inspect":
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "external": {"IPAddress": "172.20.0.3"},
                            "offline": {"IPAddress": "172.19.0.3"},
                        }
                    ),
                    stderr="",
                )
            network_name = command[-1]
            return SimpleNamespace(
                returncode=0,
                stdout="true\n" if network_name == "offline" else "false\n",
                stderr="",
            )

        with patch("agents.port_forwarder.subprocess.run", side_effect=fake_run):
            self.assertEqual(_resolve_internal_container_ip("whisper"), "172.19.0.3")

    def test_rejects_container_without_internal_network(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            self.assertEqual(
                set(_kwargs),
                {"capture_output", "check", "text", "timeout"},
            )
            if command[1] == "inspect":
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"bridge": {"IPAddress": "172.19.0.3"}}),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="false\n", stderr="")

        with (
            patch("agents.port_forwarder.subprocess.run", side_effect=fake_run),
            self.assertRaisesRegex(RuntimeError, "rede interna"),
        ):
            _resolve_internal_container_ip("whisper")


if __name__ == "__main__":
    unittest.main()
