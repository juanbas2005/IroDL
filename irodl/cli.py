"""CLI de IroDL: empaquetar, inspeccionar y descargar paquetes .irodl.

    python -m irodl pack   <archivo>  -o salida/ [--prefix pk/dd/usuario]
    python -m irodl fetch  <p.irodl> -o salida/ [--host https://...] [-c 4]
    python -m irodl inspect <p.irodl> [--show-paths]
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .chunker import local_pack
from .crypto import decrypt_token
from .downloader import IrodlDownloader, IrodlError
from .format import load_manifest


def _human(n: float) -> str:
    for u in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or u == "GiB":
            return f"{n:.1f} {u}" if u != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GiB"


def _progress(done_b, total_b, done_c, total_c, speed, state):
    pct = (done_b / total_b * 100) if total_b else 100.0
    sys.stdout.write(
        f"\r  {state[:38]:<38} {pct:6.2f}%  {done_c}/{total_c} chunks  "
        f"{_human(speed) if speed else '-'}/s   ")
    sys.stdout.flush()


def cmd_pack(args) -> int:
    p = Path(args.file)
    if not p.is_file():
        print(f"no existe: {p}", file=sys.stderr)
        return 2
    t0 = time.time()
    m, out = local_pack(p, args.out, prefix=args.prefix)
    print(f"\nempaquetado: {out}")
    print(f"  chunks   : {m.n_chunks} x {_human(m.chunk_size)}")
    print(f"  filesize : {_human(m.filesize)}")
    print(f"  md5      : {m.md5}")
    print(f"  en {time.time() - t0:.2f}s")
    return 0


def cmd_fetch(args) -> int:
    m = load_manifest(args.manifest)
    print(f"  archivo  : {m.filename}")
    print(f"  tamaño   : {_human(m.filesize)} en {m.n_chunks} chunks")
    cancel = threading.Event()
    try:
        dl = IrodlDownloader(m, args.out, host=args.host, concurrency=args.concurrency,
                             progress=_progress, cancel=cancel)
        out = dl.run()
        print(f"\nOK → {out}")
        return 0
    except KeyboardInterrupt:
        cancel.set()
        print("\ncancelado", file=sys.stderr)
        return 130
    except IrodlError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


def cmd_inspect(args) -> int:
    m = load_manifest(args.manifest)
    print(f"archivo   : {m.filename}")
    print(f"tamaño    : {m.filesize} bytes ({_human(m.filesize)})")
    print(f"chunks    : {m.n_chunks} x {_human(m.chunk_size)}")
    print(f"md5 global: {m.md5}")
    print(f"host      : {m.host}")
    print(f"app/format: {m.app} / {m.format} v{m.version}")
    for i, c in enumerate(m.chunks):
        extra = ""
        if args.show_paths:
            try:
                extra = f"  → {decrypt_token(c.token)}"
            except Exception as exc:
                extra = f"  (token no descifrable: {exc})"
        print(f"  [{i:03d}] {c.name}  md5={c.md5}{extra}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="irodl", description="Paquetes de descarga .irodl")
    ap.add_argument("--version", action="version", version=f"irodl {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("pack", help="archivo → chunks 2 MiB + .irodl")
    p1.add_argument("file")
    p1.add_argument("-o", "--out", default=".", help="directorio de salida")
    p1.add_argument("--prefix", default="pk/dd/local", help="prefijo de rutas en tokens")
    p1.set_defaults(fn=cmd_pack)

    p2 = sub.add_parser("fetch", help=".irodl → archivo original")
    p2.add_argument("manifest")
    p2.add_argument("-o", "--out", default=".")
    p2.add_argument("--host", default=None, help="base de descarga alternativa (mirror)")
    p2.add_argument("-c", "--concurrency", type=int, default=4)
    p2.set_defaults(fn=cmd_fetch)

    p3 = sub.add_parser("inspect", help="ver contenido del paquete")
    p3.add_argument("manifest")
    p3.add_argument("--show-paths", action="store_true", help="descifrar rutas (requiere clave)")
    p3.set_defaults(fn=cmd_inspect)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
