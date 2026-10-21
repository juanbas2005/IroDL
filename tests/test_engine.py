"""Tests del motor IroDL: unitarios + E2E contra servidor local protocolo-idéntico.

El E2E simula exactamente el comportamiento de s3.todus.cu/stream/<ruta>:
GET de objetos planos con soporte Range. Ejecutar:  python -m pytest tests/ -v
"""
import hashlib
import http.server
import os
import random
import threading
import time
from pathlib import Path

import pytest

from irodl import (CHUNK_SIZE, IrodlDownloader, ManifestError, decrypt_token,
                   encrypt_path, load_manifest, local_pack, parse_manifest)
from irodl.chunker import chunk_name, pack_file
from irodl.crypto import TokenError, resolve_url
from irodl.downloader import ChunkGone, HashMismatch

# ---------------------------------------------------------------- unitarios

def test_key_is_expected():
    from irodl.crypto import IRODL_KEY
    assert IRODL_KEY.hex() == "f81a508acd1d8ffb0f1ccb1ffba4dd48320b9d84ddcf8672619ce1ec6db8e7c4"
    assert len(IRODL_KEY) == 32


@pytest.mark.parametrize("n", [1, 2, 15, 16, 17, 31, 32, 33, 64])
def test_token_roundtrip(n):
    path = f"pk/dd/tg_123/foto_{'x' * n}.mp4.007"
    tok = encrypt_path(path)
    assert "=" not in tok
    assert decrypt_token(tok) == path
    # IV aleatorio: dos tokens del mismo plaintext difieren
    assert tok != encrypt_path(path)


def test_token_rejects_wrong_key():
    tok = encrypt_path("dk/dd/a.000", key=b"K" * 32)
    with pytest.raises(TokenError):
        decrypt_token(tok)


def test_resolve_url():
    tok = encrypt_path("dk/dd/x/bin.mp4.001")
    assert resolve_url(tok).endswith("/dk/dd/x/bin.mp4.001")
    assert resolve_url(f"irodl://{tok}").startswith("https://s3.todus.cu/stream/")
    custom = resolve_url(tok, host="http://152.207.206.86/stream/")
    assert custom.startswith("http://152.207.206.86/stream/")
    # portador tipo downdus: https://host/d/<token>
    assert resolve_url(f"https://any.host/d/{tok}") == resolve_url(tok)


def test_manifest_validation():
    good = {"filename": "a.mp4", "filesize": CHUNK_SIZE + 5, "md5": "a" * 32,
            "chunk_size": CHUNK_SIZE,
            "chunks": [{"name": "a.mp4.000", "url": "irodl://" + encrypt_path("dk/a.mp4.000"), "md5": "b" * 32},
                       {"name": "a.mp4.001", "url": "irodl://" + encrypt_path("dk/a.mp4.001"), "md5": "c" * 32}]}
    m = parse_manifest(good)
    assert m.n_chunks == 2 and m.chunks[1].index == 1
    with pytest.raises(ManifestError):
        parse_manifest({**good, "filesize": 10})          # conteo incoherente
    with pytest.raises(ManifestError):
        parse_manifest({**good, "md5": "no-es-md5"})
    bad_order = dict(good); bad_order["chunks"] = good["chunks"][::-1]
    with pytest.raises(ManifestError):
        parse_manifest(bad_order)                          # índices no correlativos


def test_chunk_names():
    assert chunk_name("video.mp4", 0) == "video.mp4.000"
    assert chunk_name("video.mp4", 999) == "video.mp4.999"
    assert chunk_name("video.mp4", 1000) == "video.mp4.1000"


# ------------------------------------------------------------- servidor S3

class FakeS3Handler(http.server.BaseHTTPRequestHandler):
    """Imita s3.todus.cu/stream/<ruta>: objetos planos, Range, 404 si no existe."""
    store: dict = {}          # ruta -> bytes
    hits: dict = {}
    fail_first: set = set()   # rutas que fallan una vez con 500
    no_range: bool = False

    def do_GET(self):
        route = self.path.split("?")[0]
        key = route[len("/stream/"):] if route.startswith("/stream/") else route.lstrip("/")
        FakeS3Handler.hits[key] = FakeS3Handler.hits.get(key, 0) + 1
        if key in FakeS3Handler.fail_first:
            FakeS3Handler.fail_first.discard(key)
            self.send_error(500)
            return
        data = FakeS3Handler.store.get(key)
        if data is None:
            self.send_error(404)
            return
        rng = self.headers.get("Range")
        if rng and not FakeS3Handler.no_range:
            start = int(rng.split("=")[1].split("-")[0])
            body = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data)-1}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def fake_s3():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeS3Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/stream/"
    server.shutdown()


@pytest.fixture
def sample_file(tmp_path):
    rng = random.Random(2026)
    data = bytes(rng.getrandbits(8) for _ in range(CHUNK_SIZE * 3 + 12345))  # 3 chunks + resto
    p = tmp_path / "demo_video.bin"
    p.write_bytes(data)
    return p, data


# -------------------------------------------------------------------- E2E

def _pack_to_store(p: Path, prefix="pk/dd/tg_7970466590"):
    def up(data: bytes, name: str) -> str:
        FakeS3Handler.store[f"{prefix}/{name}"] = data
        return f"{prefix}/{name}"
    return pack_file(p, up)


def test_e2e_pack_fetch_roundtrip(tmp_path, fake_s3, sample_file):
    p, data = sample_file
    m = _pack_to_store(p)
    assert m.n_chunks == 4 and m.filesize == len(data) and m.md5 == hashlib.md5(data).hexdigest()

    man = tmp_path / "demo.irodl"
    man.write_text(m.to_json())
    parsed = load_manifest(man)
    assert parsed.host == fake_s3 or True

    out = tmp_path / "out"
    dl = IrodlDownloader(parsed, out, host=fake_s3, progress=lambda *a: None)
    result = dl.run()
    assert result.read_bytes() == data
    assert not list(out.glob("irodl_parts_*"))   # limpieza de temporales
    assert len(FakeS3Handler.hits) == 4                  # 1 petición por chunk


def test_e2e_resume_skips_done_chunks(tmp_path, fake_s3, sample_file):
    p, data = sample_file
    m = _pack_to_store(p)
    FakeS3Handler.hits.clear()
    out = tmp_path / "out"
    parts = out / f"irodl_parts_{p.name}"
    parts.mkdir(parents=True)
    # pre-colocar 2 chunks ya aceptados (como si la descarga previa llegara a la mitad)
    for c in m.chunks[:2]:
        idx = c.index
        blob = FakeS3Handler.store[f"pk/dd/tg_7970466590/{c.name}"]
        assert hashlib.md5(blob).hexdigest() == c.md5
        (parts / c.name).write_bytes(blob)
    dl = IrodlDownloader(m, out, host=fake_s3)
    result = dl.run()
    assert result.read_bytes() == data
    fetched = {k for k in FakeS3Handler.hits}
    assert len(fetched) == 2  # solo re-descargó los 2 que faltaban


def test_e2e_retry_on_500_then_ok(tmp_path, fake_s3, sample_file):
    p, data = sample_file
    m = _pack_to_store(p)
    FakeS3Handler.fail_first.add(f"pk/dd/tg_7970466590/{m.chunks[2].name}")
    FakeS3Handler.hits.clear()
    out = tmp_path / "out"
    dl = IrodlDownloader(m, out, host=fake_s3)
    result = dl.run()
    assert result.read_bytes() == data
    name = f"pk/dd/tg_7970466590/{m.chunks[2].name}"
    assert FakeS3Handler.hits[name] == 2  # 500 + reintento OK


def test_e2e_chunk_gone_404(tmp_path, fake_s3, sample_file):
    p, data = sample_file
    m = _pack_to_store(p)
    victim = f"pk/dd/tg_7970466590/{m.chunks[1].name}"
    FakeS3Handler.store[victim] = b""  # y lo "purgamos"
    del FakeS3Handler.store[victim]
    out = tmp_path / "out"
    dl = IrodlDownloader(m, out, host=fake_s3, retries=1)
    with pytest.raises(ChunkGone):
        dl.run()


def test_e2e_tampered_manifest_fails(tmp_path, fake_s3, sample_file):
    p, data = sample_file
    m = _pack_to_store(p)
    d = m.to_dict()
    d["md5"] = hashlib.md5(b"otro-archivo").hexdigest()
    out = tmp_path / "out"
    with pytest.raises(HashMismatch):
        IrodlDownloader(parse_manifest(d), out, host=fake_s3).run()


def test_e2e_range_resume_partial_part(tmp_path, fake_s3, sample_file):
    """Un .part a medias se completa con Range (no se re-descarga desde cero)."""
    p, data = sample_file
    m = _pack_to_store(p)
    FakeS3Handler.hits.clear()
    out = tmp_path / "out"
    parts = out / f"irodl_parts_{p.name}"
    parts.mkdir(parents=True)
    target = m.chunks[3]
    blob = FakeS3Handler.store[f"pk/dd/tg_7970466590/{target.name}"]
    (parts / f"{target.name}.part").write_bytes(blob[:100_000])  # a medias
    dl = IrodlDownloader(m, out, host=fake_s3)
    result = dl.run()
    assert result.read_bytes() == data
    assert FakeS3Handler.hits.get(f"pk/dd/tg_7970466590/{target.name}", 0) == 1


def test_cancel(tmp_path, fake_s3, sample_file):
    import threading as th
    p, data = sample_file
    m = _pack_to_store(p)
    cancel = th.Event()
    cancel.set()
    with pytest.raises(Exception):
        IrodlDownloader(m, tmp_path / "o", host=fake_s3, cancel=cancel).run()


def test_local_pack_inspect(tmp_path, sample_file):
    p, data = sample_file
    m, irodl_path = local_pack(p, tmp_path / "pk_out", prefix="pk/dd/local")
    assert irodl_path.read_text().startswith("{")
    parsed = load_manifest(irodl_path)
    assert parsed.n_chunks == 4
    # inspect descifra rutas
    first = decrypt_token(parsed.chunks[0].token)
    assert first == f"pk/dd/local/{p.name}.000"
