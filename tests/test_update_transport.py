from __future__ import annotations

import asyncio
import hashlib
import json
import select
import socket
import ssl
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest

from vibeocr.classic.services.update_transport import (
    HttpxFeedMaterializer,
    UpdateSourceCandidate,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


# Public, test-only localhost CA/certificate generated with trustme 1.2.1.
_CA_CERT = """-----BEGIN CERTIFICATE-----
MIIByDCCAW+gAwIBAgIUV4PKQlN7ISoJBvf0q1wV7iet6mwwCgYIKoZIzj0EAwIw
QDEXMBUGA1UECgwOdHJ1c3RtZSB2MS4yLjExJTAjBgNVBAsMHFRlc3RpbmcgQ0Eg
I095U2d0aGl5M3hiQk16X1owIBcNMDAwMTAxMDAwMDAwWhgPMzAwMDAxMDEwMDAw
MDBaMEAxFzAVBgNVBAoMDnRydXN0bWUgdjEuMi4xMSUwIwYDVQQLDBxUZXN0aW5n
IENBICNPeVNndGhpeTN4YkJNel9aMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE
XHqX4Pg42dlv+WLDCFmNZNX87gwSeG6GZf4uyDrPHgcAFFgKIyl+IHkWR5iKahFO
Daph/Vzm1lntkrxZYFhlXaNFMEMwHQYDVR0OBBYEFF1/rkElb8cniuMVN4zAO8Lz
mCDBMBIGA1UdEwEB/wQIMAYBAf8CAQkwDgYDVR0PAQH/BAQDAgGGMAoGCCqGSM49
BAMCA0cAMEQCICQhQTz6p3DymmOVX1+kqFbT2CnyAEckZFBsSVB7BRbPAiA+zrV5
fCZr2S/MALpZF6n9fworBy556nj4VhAiImkp1A==
-----END CERTIFICATE-----
"""
_SERVER_CERT = """-----BEGIN CERTIFICATE-----
MIICLTCCAdOgAwIBAgIUFKpKS20nnRBj1HhlH6rM+aIndrMwCgYIKoZIzj0EAwIw
QDEXMBUGA1UECgwOdHJ1c3RtZSB2MS4yLjExJTAjBgNVBAsMHFRlc3RpbmcgQ0Eg
I095U2d0aGl5M3hiQk16X1owIBcNMDAwMTAxMDAwMDAwWhgPMzAwMDAxMDEwMDAw
MDBaMEIxFzAVBgNVBAoMDnRydXN0bWUgdjEuMi4xMScwJQYDVQQLDB5UZXN0aW5n
IGNlcnQgI0htX1Fva0pPYlU5WkVvVFgwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNC
AAT7/u9p8t+9nPgYTa4RrxROxIjtbVEHXRG6SVmj0YhWr9CIVzGgb5uQFKhywDNf
2LchYOyVsa1/aqSXsnRmsq1Do4GmMIGjMB0GA1UdDgQWBBRQ+NNN9C1tmmADXypJ
VnNKfd0cQDAMBgNVHRMBAf8EAjAAMB8GA1UdIwQYMBaAFF1/rkElb8cniuMVN4zA
O8LzmCDBMBcGA1UdEQEB/wQNMAuCCWxvY2FsaG9zdDAOBgNVHQ8BAf8EBAMCBaAw
KgYDVR0lAQH/BCAwHgYIKwYBBQUHAwIGCCsGAQUFBwMBBggrBgEFBQcDAzAKBggq
hkjOPQQDAgNIADBFAiEA3QgwWHaTtduoehusajm31Op0FhOJ3ma1i+hEquARxvAC
IFXtz4ICSZph+xNZ+1YU0Yc1xQqafVMG46RY9tgyBjH+
-----END CERTIFICATE-----
"""
_SERVER_KEY = """-----BEGIN EC PRIVATE KEY-----
MHcCAQEEIGXheZN+WDu8Hke3DBj0DPv8vaBFvWx5nrBxdJOcPfD1oAoGCCqGSM49
AwEHoUQDQgAE+/7vafLfvZz4GE2uEa8UTsSI7W1RB10RuklZo9GIVq/QiFcxoG+b
kBSocsAzX9i3IWDslbGtf2qkl7J0ZrKtQw==
-----END EC PRIVATE KEY-----
"""


@contextmanager
def _https_release_server(
    tmp_path: Path, package: bytes
) -> Iterator[tuple[str, Path, list[str]]]:
    ca = tmp_path / "ca.pem"
    certificate = tmp_path / "server.pem"
    key = tmp_path / "server-key.pem"
    ca.write_text(_CA_CERT, encoding="ascii")
    certificate.write_text(_SERVER_CERT, encoding="ascii")
    key.write_text(_SERVER_KEY, encoding="ascii")
    observed: list[str] = []
    feed = json.dumps(
        {
            "Assets": [
                {
                    "PackageId": "VibeOCRClassic",
                    "Version": "1.2.3",
                    "Type": "Full",
                    "FileName": "VibeOCRClassic-1.2.3-full.nupkg",
                    "SHA256": hashlib.sha256(package).hexdigest().upper(),
                    "Size": len(package),
                }
            ]
        }
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append(self.path)
            if self.path.endswith("/latest/download/releases.win.json"):
                payload = feed
            elif self.path.endswith("/download/v1.2.3/VibeOCRClassic-1.2.3-full.nupkg"):
                payload = package
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}", ca, observed
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@contextmanager
def _connect_proxy() -> Iterator[tuple[str, list[str]]]:
    observed: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self) -> None:
            observed.append(self.path)
            host, port = self.path.rsplit(":", 1)
            upstream = socket.create_connection((host, int(port)), timeout=5)
            try:
                self.send_response(200, "Connection Established")
                self.end_headers()
                sockets = (self.connection, upstream)
                while True:
                    readable, _, _ = select.select(sockets, (), (), 5)
                    if not readable:
                        break
                    for source in readable:
                        payload = source.recv(65536)
                        if not payload:
                            return
                        target = (
                            upstream if source is self.connection else self.connection
                        )
                        target.sendall(payload)
            finally:
                upstream.close()

        def log_message(self, _format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", observed
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_materialized_source_uses_https_connect_for_feed_and_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = b"full nupkg chunk" * (512 * 1024)
    progress: list[int] = []
    with _https_release_server(tmp_path, package) as (origin, ca, origin_requests):
        with _connect_proxy() as (proxy, connect_requests):
            monkeypatch.setenv("HTTPS_PROXY", proxy)
            monkeypatch.setenv("NO_PROXY", "")
            monkeypatch.setenv("SSL_CERT_FILE", str(ca))
            materializer = HttpxFeedMaterializer(tmp_path / "materialized")

            source = asyncio.run(
                materializer.materialize(
                    (
                        UpdateSourceCandidate(
                            "direct", f"{origin}/releases/latest/download/"
                        ),
                    )
                )
            )
            try:
                assert not any("full.nupkg" in path for path in origin_requests)
                asyncio.run(source.download(progress=progress.append))
                assert source.base_url.startswith("http://127.0.0.1:")
                materialized = (
                    tmp_path / "materialized" / "VibeOCRClassic-1.2.3-full.nupkg"
                )
                assert materialized.read_bytes() == package
            finally:
                source.close()

    assert len(connect_requests) >= 2
    assert "/releases/latest/download/releases.win.json" in origin_requests
    assert (
        "/releases/download/v1.2.3/VibeOCRClassic-1.2.3-full.nupkg" in origin_requests
    )
    assert progress[0] > 0
    assert progress[-1] == 100
    assert len(progress) > 2


def test_materialized_package_cancellation_removes_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = b"cancel-me" * (1024 * 1024)
    cancelled = asyncio.Event()
    with _https_release_server(tmp_path, package) as (origin, ca, _):
        monkeypatch.setenv("NO_PROXY", "localhost")
        monkeypatch.setenv("SSL_CERT_FILE", str(ca))
        source = asyncio.run(
            HttpxFeedMaterializer(tmp_path / "materialized").materialize(
                (
                    UpdateSourceCandidate(
                        "direct", f"{origin}/releases/latest/download/"
                    ),
                )
            )
        )

        def cancel_after_first_chunk(value: int) -> None:
            if value > 0:
                cancelled.set()

        try:
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(
                    source.download(
                        progress=cancel_after_first_chunk,
                        cancel_event=cancelled,
                    )
                )
        finally:
            source.close()

    assert not (tmp_path / "materialized" / "VibeOCRClassic-1.2.3-full.nupkg").exists()
    assert not list((tmp_path / "materialized").glob(".*.partial"))


def test_materialized_package_preflights_target_volume_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = b"space-required"
    with _https_release_server(tmp_path, package) as (origin, ca, _):
        monkeypatch.setenv("NO_PROXY", "localhost")
        monkeypatch.setenv("SSL_CERT_FILE", str(ca))
        source = asyncio.run(
            HttpxFeedMaterializer(
                tmp_path / "materialized", available_bytes=lambda _path: 0
            ).materialize(
                (
                    UpdateSourceCandidate(
                        "direct", f"{origin}/releases/latest/download/"
                    ),
                )
            )
        )
        try:
            with pytest.raises(ValueError, match="空间不足"):
                asyncio.run(source.download())
        finally:
            source.close()

    assert not (tmp_path / "materialized" / "VibeOCRClassic-1.2.3-full.nupkg").exists()
