import 'dart:io';

import 'package:flutter/material.dart';
import 'package:open_filex/open_filex.dart';
import 'package:share_plus/share_plus.dart';

import '../irodl/downloader.dart';
import '../irodl/format.dart';
import '../main.dart';
import 'home_screen.dart';

/// Pantalla de progreso (y de resultado para descargas ya terminadas).
class TaskScreen extends StatefulWidget {
  final IrodlManifest? manifest;
  final DownloadRecord? completed;
  final void Function(String path, int size)? onFinished;

  const TaskScreen({super.key, required this.manifest, this.onFinished})
      : completed = null;

  const TaskScreen.completed(this.completed, {super.key})
      : manifest = null,
        onFinished = null;

  @override
  State<TaskScreen> createState() => _TaskScreenState();
}

class _TaskScreenState extends State<TaskScreen> {
  IrodlTask? _task;
  TaskProgress? _progress;
  String? _error;
  String? _donePath;
  bool _started = false;

  @override
  void initState() {
    super.initState();
    if (widget.manifest != null) {
      _begin();
    } else {
      _donePath = widget.completed!.path;
    }
  }

  Future<void> _begin() async {
    final m = widget.manifest!;
    final dir = await irodlDownloadsDir();
    final task = IrodlTask(
      manifest: m,
      destDir: dir,
      onProgress: (p) {
        if (mounted) setState(() => _progress = p);
      },
    );
    setState(() {
      _task = task;
      _started = true;
    });
    try {
      final out = await task.run();
      if (!mounted) return;
      setState(() => _donePath = out.path);
      widget.onFinished?.call(out.path, out.lengthSync());
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  Future<void> _open() async {
    if (_donePath == null) return;
    await OpenFilex.open(_donePath!);
  }

  Future<void> _share() async {
    if (_donePath == null) return;
    await Share.shareXFiles([XFile(_donePath!)],
        text: 'Archivo reconstruido con IroDL');
  }

  Future<void> _confirmCancel() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('¿Cancelar descarga?'),
        content: const Text(
            'Los chunks ya descargados se conservan: podrás reanudar '
            'seleccionando el mismo paquete .irodl.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Seguir')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Cancelar descarga')),
        ],
      ),
    );
    if (ok == true && mounted) {
      _task?.cancel();
      Navigator.of(context).pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final finished = _donePath != null;
    return PopScope(
      canPop: finished,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && !finished) _confirmCancel();
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(widget.manifest?.filename ??
              widget.completed?.name ??
              'Descarga'),
          actions: [
            if (!finished)
              IconButton(
                icon: const Icon(Icons.close),
                onPressed: _confirmCancel,
              ),
          ],
        ),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: _body(context),
          ),
        ),
      ),
    );
  }

  Widget _body(BuildContext context) {
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off, size: 64, color: Colors.redAccent),
            const SizedBox(height: 16),
            Text('La descarga falló',
                style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Text(
                _error!,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white70),
              ),
            ),
            const SizedBox(height: 20),
            OutlinedButton(
              onPressed: () {
                setState(() {
                  _error = null;
                  _progress = null;
                });
                _begin();
              },
              child: const Text('Reintentar'),
            ),
          ],
        ),
      );
    }

    if (finished) {
      final size = widget.completed?.size ??
          (_donePath != null ? File(_donePath!).lengthSync() : 0);
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.verified, size: 72, color: Color(0xFF22D3EE)),
            const SizedBox(height: 14),
            Text('Descarga verificada',
                style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 6),
            Text('MD5 por chunk y global correctos',
                style: const TextStyle(color: Colors.white54)),
            Text(_donePath ?? '',
                textAlign: TextAlign.center,
                style: const TextStyle(
                    color: Colors.white38, fontSize: 12)),
            const SizedBox(height: 26),
            Row(children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: _open,
                  icon: const Icon(Icons.open_in_new),
                  label: const Text('Abrir'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _share,
                  icon: const Icon(Icons.share),
                  label: const Text('Compartir'),
                ),
              ),
            ]),
            Text(humanSize(size),
                style: const TextStyle(color: Colors.white54)),
          ],
        ),
      );
    }

    final p = _progress;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: 8),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(p?.status ?? 'preparando…',
                    style: const TextStyle(color: Colors.white70)),
                const SizedBox(height: 14),
                ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: LinearProgressIndicator(
                    value: p?.fraction ?? 0,
                    minHeight: 12,
                    backgroundColor: const Color(0xFF0B1220),
                    valueColor: const AlwaysStoppedAnimation(Color(0xFF22D3EE)),
                  ),
                ),
                const SizedBox(height: 14),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                        '${humanSize(p?.bytesDone ?? 0)} / '
                        '${humanSize(widget.manifest!.filesize)}',
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    Text(humanSpeed(p?.speedBps ?? 0),
                        style: const TextStyle(color: Colors.white70)),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  'chunks ${p?.chunksDone ?? 0}/${p?.chunksTotal ?? 0} · '
                  '${((p?.fraction ?? 0) * 100).toStringAsFixed(1)}%',
                  style: const TextStyle(color: Colors.white54),
                ),
              ],
            ),
          ),
        ),
        const Spacer(),
        OutlinedButton.icon(
          onPressed: _confirmCancel,
          icon: const Icon(Icons.stop_circle_outlined),
          label: const Text('Cancelar'),
        ),
      ],
    );
  }
}
