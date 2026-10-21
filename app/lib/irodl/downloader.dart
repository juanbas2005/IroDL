import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:pointycastle/export.dart';

import 'crypto.dart';
import 'format.dart';

/// MD5 en streaming de un archivo (sin cargar el archivo completo en RAM).
Future<String> md5OfFile(File f) async {
  final d = MD5Digest();
  await for (final block in f.openRead()) {
    final u = block is Uint8List ? block : Uint8List.fromList(block);
    d.update(u, 0, u.length);
  }
  final out = Uint8List(16);
  d.doFinal(out, 0);
  return out.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
}

String _hexOf(Uint8List bytes) =>
    bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

/// Motor de descarga .irodl para la app.
/// Mismo diseño que el original invertido: paralelo (4 conexiones),
/// MD5 por chunk, reintentos con Range, reensamblado con MD5 global.
class TaskProgress {
  final int bytesDone;
  final int bytesTotal;
  final int chunksDone;
  final int chunksTotal;
  final double speedBps;
  final String status;

  const TaskProgress({
    required this.bytesDone,
    required this.bytesTotal,
    required this.chunksDone,
    required this.chunksTotal,
    required this.speedBps,
    required this.status,
  });

  double get fraction =>
      bytesTotal > 0 ? (bytesDone / bytesTotal).clamp(0.0, 1.0) : 0.0;
}

class IrodlTaskException implements Exception {
  final String message;
  IrodlTaskException(this.message);
  @override
  String toString() => message;
}

class IrodlTask {
  final IrodlManifest manifest;
  final Directory destDir;
  final String? host;
  final int concurrency;
  final void Function(TaskProgress)? onProgress;

  bool _cancelled = false;
  int _bytesDone = 0;
  int _chunksDone = 0;
  double _speed = 0;
  String _status = 'preparando';
  DateTime _lastReport = DateTime.now();

  IrodlTask({
    required this.manifest,
    required this.destDir,
    this.host,
    this.concurrency = 4,
    this.onProgress,
  });

  void cancel() => _cancelled = true;

  Directory get _partsDir {
    final safe = manifest.filename.replaceAll(
        RegExp(r'[^A-Za-z0-9._-]'), '_');
    final d = Directory(
        '${destDir.path}${Platform.pathSeparator}irodl_parts_$safe');
    d.createSync(recursive: true);
    return d;
  }

  String get _statusText => _status;

  void _report({bool force = false}) {
    final now = DateTime.now();
    if (!force && now.difference(_lastReport).inMilliseconds < 300) return;
    _lastReport = now;
    onProgress?.call(TaskProgress(
      bytesDone: _bytesDone,
      bytesTotal: manifest.filesize,
      chunksDone: _chunksDone,
      chunksTotal: manifest.nChunks,
      speedBps: _speed,
      status: _status,
    ));
  }

  File _doneFile(IrodlChunk c) => File('${_partsDir.path}${Platform.pathSeparator}${c.name}');
  File _partFile(IrodlChunk c) => File('${_doneFile(c).path}.part');

  /// Valida todos los tokens antes de tocar la red.
  void validateTokens() {
    for (final c in manifest.chunks) {
      IrodlCrypto.resolveUrl(c.token, host: host);
    }
  }

  Future<File> run() async {
    destDir.createSync(recursive: true);
    validateTokens();
    _status = 'descargando';
    _report(force: true);

    final client = http.Client();
    try {
      final queue = List<IrodlChunk>.from(manifest.chunks);
      final failures = <String>[];
      int next = 0;

      Future<void> worker() async {
        while (next < queue.length) {
          if (_cancelled) return;
          final c = queue[next++];
          try {
            await _downloadChunk(client, c);
            _chunksDone++;
            _status = 'chunks $_chunksDone/${manifest.nChunks}';
            _report(force: true);
          } catch (e) {
            if (_cancelled) return;
            failures.add('${c.name}: $e');
          }
        }
      }

      await Future.wait(
          List.generate(concurrency.clamp(1, 8), (_) => worker()));

      if (_cancelled) throw IrodlTaskException('Descarga cancelada');
      if (failures.isNotEmpty) {
        throw IrodlTaskException('Chunks con error: '
            '${failures.take(3).join(' · ')}');
      }

      final out = await _reassemble();
      return out;
    } finally {
      client.close();
    }
  }

  Future<void> _downloadChunk(http.Client client, IrodlChunk c) async {
    final done = _doneFile(c);
    if (done.existsSync()) {
      final digest = await md5OfFile(done);
      if (digest == c.md5) {
        _bytesDone += done.lengthSync();
        _report();
        return; // resume: chunk ya verificado
      }
      done.deleteSync();
    }

    final part = _partFile(c);
    for (var attempt = 1; attempt <= 3; attempt++) {
      if (_cancelled) throw IrodlTaskException('cancelado');
      try {
        await _fetchOnce(client, c, part);
        final digest = await md5OfFile(part);
        if (digest != c.md5) {
          throw IrodlTaskException('md5 del chunk no coincide');
        }
        part.renameSync(done.path);
        return;
      } on IrodlTaskException {
        rethrow;
      } catch (e) {
        if (attempt == 3) rethrow;
        await Future<void>.delayed(Duration(milliseconds: 900 * attempt));
      }
    }
  }

  Future<void> _fetchOnce(http.Client client, IrodlChunk c, File part) async {
    final uri = Uri.parse(
        IrodlCrypto.resolveUrl(c.token, host: host ?? IrodlManifest.defaultHost));
    var offset = part.existsSync() ? part.lengthSync() : 0;
    final req = http.Request('GET', uri)
      ..headers['User-Agent'] = 'IroDL/1.0';
    if (offset > 0) req.headers['Range'] = 'bytes=$offset-';

    final resp = await client.send(req).timeout(const Duration(seconds: 30));
    if (resp.statusCode == 404) {
      throw IrodlTaskException('chunk no disponible (404, ¿vencido?)');
    }
    if (offset > 0 && resp.statusCode == 200) {
      offset = 0; // servidor no soportó Range: desde cero
    }
    if (resp.statusCode != 200 && resp.statusCode != 206) {
      throw IrodlTaskException('HTTP ${resp.statusCode}');
    }

    // El MD5 canónico del chunk se verifica sobre el archivo en _downloadChunk.
    _status = 'descargando ${c.name}';
    final tmp = File('${part.path}.new');
    final sink = (offset > 0)
        ? part.openWrite(mode: FileMode.append)
        : tmp.openWrite();
    final sw = Stopwatch()..start();
    var received = 0;
    try {
      await for (final chunk in resp.stream) {
        if (_cancelled) throw IrodlTaskException('cancelado');
        sink.add(chunk);
        received += chunk.length;
        _bytesDone += chunk.length;
        final secs = sw.elapsedMilliseconds / 1000.0;
        if (secs > 0.2) {
          _speed = _speed * 0.6 + (received / secs) * 0.4;
          received = 0;
          sw.reset();
        }
        _report();
      }
      await sink.flush();
      await sink.close();
      if (offset == 0) {
        tmp.renameSync(part.path);
      }
    } catch (_) {
      try {
        await sink.close();
      } catch (_) {}
      if (offset == 0 && tmp.existsSync()) tmp.deleteSync();
      rethrow;
    }
  }

  Future<File> _reassemble() async {
    _status = 'reensamblando';
    _report(force: true);
    final finalPath =
        '${destDir.path}${Platform.pathSeparator}${manifest.filename}';
    final tmp = File('$finalPath.rebuilding');
    final sink = tmp.openWrite();
    final digest = MD5Digest();
    var size = 0;
    try {
      for (final c in manifest.chunks) {
        final f = _doneFile(c);
        if (!f.existsSync()) {
          throw IrodlTaskException('falta el chunk ${c.name}');
        }
        await for (final block in f.openRead()) {
          final u = block is Uint8List ? block : Uint8List.fromList(block);
          sink.add(u);
          digest.update(u, 0, u.length);
          size += u.length;
        }
      }
      await sink.flush();
      await sink.close();
    } catch (_) {
      try {
        await sink.close();
      } catch (_) {}
      if (tmp.existsSync()) tmp.deleteSync();
      rethrow;
    }
    if (size != manifest.filesize) {
      if (tmp.existsSync()) tmp.deleteSync();
      throw IrodlTaskException(
          'tamaño final $size ≠ manifiesto ${manifest.filesize}');
    }
    final out16 = Uint8List(16);
    digest.doFinal(out16, 0);
    if (_hexOf(out16) != manifest.md5) {
      if (tmp.existsSync()) tmp.deleteSync();
      throw IrodlTaskException('MD5 global no coincide con el manifiesto');
    }
    final out = File(finalPath);
    if (out.existsSync()) out.deleteSync();
    tmp.renameSync(out.path);
    _partsDir.deleteSync(recursive: true);
    _status = 'completado';
    _report(force: true);
    return out;
  }
}
