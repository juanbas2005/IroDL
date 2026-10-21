"""IroDL — paquetes de descarga .irodl.

Reconstrucción limpia del motor de paquetes chunked (2 MiB, AES-256-CBC en
tokens, MD5 por chunk y global) adaptado a la infraestructura propia del bot S3.
Sin backend: el descifrado de tokens es 100% local y la descarga va directa
al servidor S3.
"""

__version__ = "1.0.0"

DEFAULT_HOST = "https://s3.todus.cu/stream/"
CHUNK_SIZE = 2 * 1024 * 1024  # 2 MiB — idéntico al sistema original

from .crypto import IRODL_KEY, IRODL_SEED, decrypt_token, encrypt_path, resolve_url
from .chunker import local_pack, pack_file
from .downloader import IrodlCancelled, IrodlDownloader
from .format import Chunk, Manifest, ManifestError, load_manifest, parse_manifest

__all__ = [
    "IRODL_SEED", "IRODL_KEY", "encrypt_path", "decrypt_token", "resolve_url",
    "CHUNK_SIZE", "DEFAULT_HOST", "Manifest", "Chunk", "ManifestError",
    "parse_manifest", "load_manifest", "pack_file", "local_pack",
    "IrodlDownloader", "IrodlCancelled", "__version__",
]
