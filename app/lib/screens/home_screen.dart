import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../irodl/format.dart';
import '../main.dart';
import 'task_screen.dart';

class DownloadRecord {
  final String name;
  final String path;
  final int size;
  DownloadRecord(this.name, this.path, this.size);
}

/// Pantalla principal: elegir .irodl → vista previa → descargar.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final List<DownloadRecord> _session = [];
  IrodlManifest? _preview;

  Future<void> _pick() async {
    final res = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['irodl'],
      withData: true,
      dialogTitle: 'Selecciona un paquete .irodl',
    );
    if (res == null || res.files.isEmpty) return;
    final file = res.files.single;
    try {
      final content = file.bytes != null
          ? String.fromCharCodes(file.bytes!)
          : File(file.path!).readAsStringSync();
      final m = IrodlManifest.parse(content);
      setState(() => _preview = m);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text('Paquete inválido: $e'),
        backgroundColor: Colors.red.shade700,
      ));
    }
  }

  void _startDownload(IrodlManifest m) {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => TaskScreen(
        manifest: m,
        onFinished: (path, size) => setState(() {
          _session.insert(0, DownloadRecord(m.filename, path, size));
        }),
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final m = _preview;
    return Scaffold(
      appBar: AppBar(title: const Text('IroDL Downloader')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            const SizedBox(height: 8),
            Center(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(28),
                child: Image.asset('assets/icon/icon.png',
                    width: 108, height: 108, fit: BoxFit.cover),
              ),
            ),
            const SizedBox(height: 18),
            const Center(
              child: Text(
                'Descarga paquetes .irodl en chunks de 2 MiB,\n'
                'con verificación MD5 por parte y global.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.white70, height: 1.4),
              ),
            ),
            const SizedBox(height: 26),
            FilledButton.icon(
              onPressed: _pick,
              icon: const Icon(Icons.folder_open),
              label: const Text('Elegir archivo .irodl'),
            ),
            const SizedBox(height: 24),
            if (m != null) ...[
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(children: [
                        const Icon(Icons.inventory_2_outlined,
                            color: Color(0xFF22D3EE)),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(m.filename,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                  fontWeight: FontWeight.w700,
                                  fontSize: 16)),
                        ),
                      ]),
                      const Divider(height: 22),
                      _row('Tamaño', humanSize(m.filesize)),
                      _row('Chunks', '${m.nChunks} × 2 MiB'),
                      _row('MD5', m.md5),
                      _row('Servidor', Uri.parse(m.host).host),
                      const SizedBox(height: 14),
                      FilledButton.icon(
                        onPressed: () => _startDownload(m),
                        icon: const Icon(Icons.download),
                        label: const Text('Iniciar descarga'),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
            ],
            if (_session.isNotEmpty) ...[
              const Text('Descargas de esta sesión',
                  style: TextStyle(
                      fontWeight: FontWeight.w700, color: Colors.white)),
              const SizedBox(height: 10),
              ..._session.map((r) => Card(
                    child: ListTile(
                      leading: const Icon(Icons.check_circle,
                          color: Color(0xFF22D3EE)),
                      title: Text(r.name,
                          maxLines: 1, overflow: TextOverflow.ellipsis),
                      subtitle: Text(humanSize(r.size)),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (_) => TaskScreen.completed(r),
                        ),
                      ),
                    ),
                  )),
            ],
            const SizedBox(height: 30),
            const Center(
              child: Text('IroDL · sin backend · formato propio .irodl',
                  style: TextStyle(color: Colors.white38, fontSize: 12)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _row(String k, String v) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
                width: 90,
                child: Text(k,
                    style: const TextStyle(color: Colors.white54))),
            Expanded(
              child: Text(v,
                  style: const TextStyle(
                      fontFamily: 'monospace', fontSize: 13)),
            ),
          ],
        ),
      );
}
