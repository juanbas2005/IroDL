import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' show sha256;
import 'package:pointycastle/export.dart';

/// Criptografía de tokens .irodl (idéntica a la del motor Python del repo).
///
/// token = b64url( IV[16] || AES-256-CBC( PKCS7( ruta ) ) )
/// key   = SHA-256(seed)[32]
class IrodlCrypto {
  /// Semilla ASCII propietaria de IroDL (ver docs/FORMATO_IRODL.md).
  static const String seed =
      'eb4c353716dbf7e763149b2fa90981bb32614eceaba1ef930ebba3cf577ad9bf';

  /// Clave AES-256 derivada de la semilla.
  static final Uint8List key =
      Uint8List.fromList(sha256.convert(utf8.encode(seed)).bytes);

  static const String scheme = 'irodl://';

  /// Referencia de verificación: key.hex() debe ser
  /// f81a508acd1d8ffb0f1ccb1ffba4dd48320b9d84ddcf8672619ce1ec6db8e7c4

  static Uint8List _b64urlDecode(String s) {
    final clean = s.trim();
    final pad = '=' * ((4 - clean.length % 4) % 4);
    return base64Url.decode(clean + pad);
  }

  static Uint8List _pkcs7Unpad(Uint8List data) {
    if (data.isEmpty || data.length % 16 != 0) {
      throw const FormatException('ciphertext inválido');
    }
    final n = data[data.length - 1];
    if (n < 1 || n > 16) throw const FormatException('padding inválido');
    for (var i = data.length - n; i < data.length; i++) {
      if (data[i] != n) throw const FormatException('padding inválido');
    }
    return Uint8List.sublistView(data, 0, data.length - n);
  }

  /// Descifra `irodl://<token>` (o token pelado) → ruta relativa.
  static String decryptToken(String token) {
    var t = token.trim();
    if (t.startsWith(scheme)) t = t.substring(scheme.length);
    final Uint8List raw;
    try {
      raw = _b64urlDecode(t);
    } catch (_) {
      throw const FormatException('token no es base64url válido');
    }
    if (raw.length < 32 || raw.length % 16 != 0) {
      throw const FormatException('token de tamaño inválido');
    }
    final iv = Uint8List.sublistView(raw, 0, 16);
    final ct = Uint8List.sublistView(raw, 16);

    final cbc = CBCBlockCipher(AESEngine())
      ..init(false, ParametersWithIV(KeyParameter(key), iv));
    final out = Uint8List(ct.length);
    var off = 0;
    while (off < ct.length) {
      off += cbc.processBlock(ct, off, out, off);
    }
    try {
      return utf8.decode(_pkcs7Unpad(out));
    } on FormatException {
      rethrow;
    } catch (_) {
      throw const FormatException('clave incorrecta o token corrupto');
    }
  }

  /// Convierte token (o portador https://…/d/<token>) en URL absoluta.
  static String resolveUrl(String tokenOrUrl, {String? host}) {
    var tok = tokenOrUrl.trim();
    if (tok.startsWith('http://') || tok.startsWith('https://')) {
      final parts = tok.split('/d/');
      tok = parts.length > 1 ? parts.last : tok;
      final q = tok.indexOf('?');
      if (q >= 0) tok = tok.substring(0, q);
    }
    final path = decryptToken(tok); // valida clave/padding
    var base = host ?? 'https://s3.todus.cu/stream/';
    if (!base.endsWith('/')) base += '/';
    return base + path.split('/').map(Uri.encodeComponent).join('/');
  }
}
