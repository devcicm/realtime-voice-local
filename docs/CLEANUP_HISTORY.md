# Cleanup log — registro de archivos eliminados

Fecha: 2026-06-15
Branch: `pre-cleanup-snapshot` (commit `774ea62`) contiene TODO el estado previo. Para restaurar cualquier archivo: `git checkout pre-cleanup-snapshot -- <archivo>` o `git reset --hard pre-cleanup-snapshot`.

## Categorias y archivos eliminados

### Categoria 1 — Logs y outputs de servicios (~30 archivos)

Borrados por ser generados en runtime, ocupan espacio, no aportan al codigo:

```
audiolibro_output.txt
backend_test_output.txt ... backend_test_output9.txt  (9 archivos)
blend_test_output.txt
chunk_inspect.txt
compare_output.txt
latency_output.txt
verify_output.txt
ws_check.txt
piper_install.txt
raw_captures/                                      (directorio entero con 4 archivos)
ws_piper.log
ws_piper_v2.log
ws_piper_v2.err.log
ws_server.log
ws_server.err.log
stt_server.log
stt_server.err.log
nul                                                (archivo vacio, nombre reservado de DOS)
```

**Justificacion**: son stdout/stderr de tests que ya hicimos. El snapshot los guarda, no se pierde nada.

### Categoria 2 — Tests y scripts de debug one-shot (16 archivos)

Borrados por ser scripts puntuales de investigacion que ya cumplieron su proposito:

```
capture_fast.py                    # one-shot: capturar formato thinking
capture_llm_raw.py                 # one-shot: ver el RAW del LLM
capture_one.py                     # one-shot: capturar 1 respuesta
capture_thinking_formats.py        # one-shot: comparar formatos thinking
probe_think_param.py               # one-shot: probe si 'think: false' funciona
probe_no_think.py                  # one-shot: probe /no_think trick
test_audio.wav                     # audio de prueba sintetico
test_denoiser.js                   # test del AudioDenoiser (ya validado)
test_sanitizer.js                  # test del Sanitizer (ya validado)
test_gemma_realtime.py             # redundante con test_realtime_e2e.py
test_realtime_mic.py               # intento fallido de grabar mic en headless
test_piper_streaming_latency.py    # pre-harness, ya superseded
```

**Justificacion**: los probe ya dieron respuesta (LM Studio no soporta `think: false` por API). Los test que se mantienen (smoke_test_ws, test_stt_e2e, test_harness_e2e, test_realtime_e2e) son los utiles para el sistema actual.

### Categoria 3 — Tests/scripts de Kokoro (no aplican) (10 archivos)

Borrados por ser del sistema Kokoro original (rama Kokoro), no del sistema actual Piper/Whisper:

```
run_chat.py                        # CLI Kokoro original
simple_chat.py                     # CLI Kokoro original
text_to_speech.py                  # CLI Kokoro original
speech_to_speech.py                # CLI Kokoro original
mix_voices.py                      # feature de voice blending (rama feats)
test_audiolibro.py                 # test Kokoro audiolibro
test_backends.py                   # test del multi-backend viejo
test_blend_verify.py               # test de voice blending
test_compare_backends.py           # test comparativo Kokoro vs Piper
test_dora.py                       # test voz Dora Kokoro
test_phoneme_diff.py               # test fonemas Kokoro
test_spanish_voices.py             # test voces espanol Kokoro
test_tts.py                        # test TTS Kokoro
test_voice_blend.py                # test blending Kokoro
```

**Justificacion**: Kokoro fue reemplazado por Piper. Estos scripts no funcionan con el sistema actual.

### Categoria 4 — Manuales redundantes (8 archivos)

Borrados por estar subsumidos en `MANUAL.md` (en esa época llamado `MANUAL_FINAL.md`):

```
MANUAL_BACKENDS_TTS.md             # manual antiguo del multi-backend
MANUAL_HARNESS_VOICE.md            # superseded por MANUAL.md
MANUAL_DE_INSTALACION.md           # manual de Kokoro (no aplica)
MANUAL_VOCES.md                    # catalogo Kokoro
MANUAL_VOCES_BLEND.md              # feature blending Kokoro
MANUAL_VOCES_ESPANOL.md            # Kokoro
manual_cambio_idioma.md            # Kokoro
"manual para cambiar idioma.txt"   # Kokoro, archivo suelto
```

**Justificacion**: `MANUAL.md` (hoy ~25KB) cubre todo lo que estos manuales decian y mas.

### Categoria 5 — Binarios y backups (4 elementos)

Borrados por ser binarios innecesarios o backups obsoletos:

```
espeak.zip                         # binario que no se usa (eSpeak NG ya instalado)
client_example_original_backup.html # backup de v1, superseded 12 veces
data/voices/*.pt                   # 5 voicepacks de Kokoro (.pt) ya no usados
nul                                # archivo vacio reservado del sistema
```

**Justificacion**: espeak.zip es de Kokoro. El backup es de v1. Los .pt son voicepacks de Kokoro (dora, alex, santa, blends) que no usa Piper.

### Categoria 6 — Repos externos (2 elementos)

```
repo/                              # git submodules de Kokoro (kokoro-tts-spanish, kokoro-tts-webui)
```

**Justificacion**: son codigo de Kokoro, no parte del sistema actual.

## Archivos que SE QUEDAN (sistema final)

### Servicios
- `stt_server.py` (microservicio STT con faster-whisper)
- `websocket_server.py` (TTS WebSocket con Piper)
- `requirements_stt.txt` (deps STT)
- `requirements.txt` (deps TTS, ya estaba)
- `data/piper_voices/` (modelos Piper: davefx-medium + otros)
- `output/` (donde Piper guarda WAVs)
- `recordings/` (audio del usuario)

### Cliente
- `client_example.html` (harness unico, v13)
- `.env` (config: TTS_BACKEND=piper)

### Documentacion
- `MANUAL.md` (documentacion completa; antes `MANUAL_FINAL.md`, renombrado)
- `README.md` (existente)
- `LICENSE` (existente)
- `CLEANUP_HISTORY.md` (este archivo)

### Codigo fuente del servidor (refactorizado para multi-backend)
- `src/core/kokoro.py`
- `src/models/*.py`
- `src/utils/__init__.py`
- `src/utils/config.py`
- `src/utils/generator.py`
- `src/utils/llm.py`
- `src/utils/speech.py`
- `src/utils/text_chunker.py`
- `src/utils/voice.py`
- `src/utils/audio.py`
- `src/utils/audio_io.py`
- `src/utils/audio_queue.py`
- `src/utils/audio_utils.py`
- `src/utils/commands.py`
- `src/utils/backends/__init__.py`
- `src/utils/backends/base.py`
- `src/utils/backends/kokoro_backend.py`
- `src/utils/backends/piper_backend.py`
- `src/utils/backends/xtts_backend.py`

### Tests utiles (los que SI valen)
- `smoke_test_ws.py` (smoke test del WS TTS)
- `test_stt_e2e.py` (smoke test STT)
- `test_harness_e2e.py` (smoke test pipeline completo STT->LLM->TTS)
- `test_realtime_e2e.py` (test realtime con gemma-4-e2b)

### Archivos varios
- `.gitignore` (existente + se agrego 'nul')

## Total

- **Antes**: ~80 archivos tracked + ~30 logs/outputs sueltos
- **Despues**: ~35 archivos tracked, todos parte del sistema actual
- **Reduccion**: ~65% menos archivos, 100% codigo util

## Como restaurar

Si necesitas cualquier archivo borrado:

```bash
# Restaurar un archivo especifico
git checkout pre-cleanup-snapshot -- path/al/archivo

# Restaurar todo (revierte este cleanup por completo)
git reset --hard pre-cleanup-snapshot

# Ver que archivos se borraron
git diff pre-cleanup-snapshot HEAD --name-only --diff-filter=D

# Restaurar el branch de snapshot como rama principal (volver atras)
git checkout main
git reset --hard pre-cleanup-snapshot
```

## Validacion post-cleanup

Comprobar que el sistema sigue funcionando:

1. STT server sigue arriba: PID 11192, puerto 8100
2. TTS server sigue arriba: PID 43200, puerto 8990
3. `client_example.html` se abre y conecta a los 3 servicios
4. JS valido: `node -c` no debe dar errores
5. Trazas, eventos y chat funcionan
