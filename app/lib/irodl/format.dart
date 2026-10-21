import 'dart:convert';

import 'package:flutter/foundation.dart';

/// Modelo y validación del manifiesto .irodl (espejo de irodl/format.py).
@immutable
class IrodlChunk {
  final String name;
  final String token;
  final String md5;
  final String url;

  const IrodlChunk({
    required this.name,
    required this.token,
    required this.md5,
    required this.url,
  });

  int get index => int.parse(name.split('.').last);

  factory IrodlChunk.fromJson(Map<String, dynamic> j, int i) {
    final name = (j['name'] ?? '').toString();
    final url = (j['url'] ?? '').toString();
    final md5 = (j['md5'] ?? '').toString().toLowerCase();
    if (name.isEmpty || url.isEmpty) {
      throw FormatException('chunk $i: faltan name/url');
    }
    if (!RegExp(r'^[0-9a-f]{32}$').hasMatch(md5)) {
      throw FormatException('chunk $i: md5 inválido');
    }
    final token = url.contains('://') ? url.split('://')[1] : url;
    if (token.isEmpty) throw FormatException('chunk $i: url sin token');
    return IrodlChunk(name: name, token: token, md5: md5, url: url);
  }
}

@immutable
class IrodlManifest {
  static const String defaultHost = 'https://s3.todus.cu/stream/';
  static const int chunkSize = 2 * 1024 * 1024; // 2 MiB

  final String filename;
  final int filesize;
  final String md5;
  final List<IrodlChunk> chunks;
  final String host;
  final String app;
  final int version;

  const IrodlManifest({
    required this.filename,
    required this.filesize,
    required this.md5,
    required this.chunks,
    this.host = defaultHost,
    this.app = 'IroDL',
    this.version = 1,
  });

  int get nChunks => chunks.length;

  int get expectedChunkCount {
    if (filesize == 0) return 0;
    final full = filesize ~/ chunkSize;
    return full + ((filesize % chunkSize == 0) ? 0 : 1);
  }

  static int _asInt(dynamic v, [int def = 1]) =>
      v is int ? v : (v is num ? v.toInt() : def);

  factory IrodlManifest.fromJson(dynamic raw) {
    if (raw is! Map<String, dynamic>) {
      // algunos pickers devuelven String JSON crudo
      if (raw is String) return IrodlManifest.fromJson(jsonDecode(raw));
      throw const FormatException('el paquete .irodl no es un objeto JSON');
    }
    final j = raw;
    final filename = (j['filename'] ?? '').toString();
    if (filename.isEmpty || filename.contains('/') || filename.contains('\\')) {
      throw const FormatException('filename inválido');
    }
    final filesize = j['filesize'];
    if (filesize is! int || filesize < 0) {
      throw const FormatException('filesize inválido');
    }
    final md5 = (j['md5'] ?? '').toString().toLowerCase();
    if (!RegExp(r'^[0-9a-f]{32}$').hasMatch(md5)) {
      throw const FormatException('md5 global inválido');
    }
    final rawChunks = j['chunks'];
    if (rawChunks is! List || rawChunks.isEmpty) {
      throw const FormatException('paquete sin chunks');
    }
    final chunks = <IrodlChunk>[];
    for (var i = 0; i < rawChunks.length; i++) {
      chunks.add(IrodlChunk.fromJson(
          Map<String, dynamic>.from(rawChunks[i] as Map), i));
    }
    // índices correlativos 0..N-1
    for (var i = 0; i < chunks.length; i++) {
      if (chunks[i].index != i) {
        throw const FormatException('índices de chunks no correlativos');
      }
    }
    final expected = IrodlManifest(
      filename: filename,
      filesize: filesize,
      md5: md5,
      chunks: chunks,
      host: (j['host'] ?? defaultHost).toString(),
      app: (j['app'] ?? 'IroDL').toString(),
      version: _asInt(j['version']),
    ).expectedChunkCount;
    if (expected != chunks.length) {
      throw FormatException(
          'filesize implica $expected chunks pero hay ${chunks.length}');
    }
    return IrodlManifest(
      filename: filename,
      filesize: filesize,
      md5: md5,
      chunks: chunks,
      host: (j['host'] ?? defaultHost).toString(),
      app: (j['app'] ?? 'IroDL').toString(),
      version: _asInt(j['version']),
    );
  }

  static IrodlManifest parse(String text) =>
      IrodlManifest.fromJson(jsonDecode(text));
}
