# Avisos de terceros y créditos

Este proyecto se apoya en el trabajo de otras personas. Aquí se reconoce su autoría y se
indica la licencia de cada componente. Las licencias de los modelos y voces son las que
publican sus autores: revísalas antes de un uso comercial.

## Proyecto original

Este repositorio nació como fork de
**[On-Device-Speech-to-Speech-Conversational-AI](https://github.com/asiff00/On-Device-Speech-to-Speech-Conversational-AI)**
de **Abdullah Al Asif** ([@asiff00](https://github.com/asiff00)), con licencia MIT
(se conserva su aviso de copyright en [LICENSE](LICENSE)). De él vienen la idea del bucle
VAD → STT → LLM → chunker → TTS, el chunker de texto por prioridades y los recursos de
`assets/`. El proyecto original agradecía a su vez a
[MiniCPM-o](https://github.com/OpenBMB/MiniCPM-o) (OpenBMB).

## Bibliotecas que se usan como dependencia (no se incluyen en el repo)

| Componente | Autor | Licencia | Uso |
|---|---|---|---|
| [piper-tts](https://github.com/OHF-Voice/piper1-gpl) | Open Home Foundation / Michael Hansen | **GPL-3.0-or-later** | Motor TTS por defecto |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | SYSTRAN | MIT | STT alternativo y VAD |
| [onnx-asr](https://github.com/istupakov/onnx-asr) | Ilya Stupakov | MIT | STT con Parakeet |
| [eSpeak NG](https://github.com/espeak-ng/espeak-ng) | eSpeak NG contributors | GPL-3.0 | Fonemizador (Kokoro) |
| [Coqui TTS](https://github.com/coqui-ai/TTS) | Coqui | MPL-2.0 (código) | Backend XTTS opcional |
| FastAPI, Starlette, uvicorn, pydantic, httpx, websockets | sus autores | MIT / BSD-3 | Servidores |
| numpy, scipy, sounddevice, soundfile, huggingface_hub | sus autores | BSD-3 / MIT / Apache-2.0 | Audio y descargas |
| Vite, Vitest, happy-dom, ESLint | sus autores | MIT | Cliente modular (desarrollo) |

**Nota sobre la GPL:** el código de este repositorio es MIT, pero `piper-tts` (y eSpeak NG)
son GPL-3.0. Aquí solo se importan como dependencias que instala el usuario, sin
redistribuirlas. Si distribuyes un paquete o ejecutable que las incluya, esa distribución
queda sujeta a la GPL-3.0.

## Modelos (se descargan en tiempo de ejecución, no se incluyen en el repo)

| Modelo | Autor | Licencia |
|---|---|---|
| [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) | NVIDIA | CC-BY-4.0 (requiere atribución) |
| [Whisper](https://github.com/openai/whisper) (vía faster-whisper / CTranslate2) | OpenAI | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) | Silero Team | MIT |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) y sus voces | hexgrad | Apache-2.0 |
| [XTTS v2](https://huggingface.co/coqui/XTTS-v2) | Coqui | **Coqui Public Model License (solo uso no comercial)** |

## Voces Piper ([rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices))

Se descargan solas desde Hugging Face al elegirlas. Licencia del dataset según el
`MODEL_CARD` de cada voz:

| Voz | Dataset | Licencia del dataset |
|---|---|---|
| es_ES/davefx-medium | [OHF-Voice/voice-datasets](https://github.com/OHF-Voice/voice-datasets) | CC0 |
| es_ES/sharvard-medium | [Sharvard corpus](https://datashare.ed.ac.uk/handle/10283/574) (Univ. de Edimburgo) | CC-BY-3.0 (requiere atribución) |
| es_ES/carlfm-x_low | [carlfm01/my-speech-datasets](https://github.com/carlfm01/my-speech-datasets) | Dominio público |
| es_ES/mls_9972-low, mls_10246-low | [Multilingual LibriSpeech](http://www.openslr.org/94/) | CC-BY-4.0 (requiere atribución) |
| es_MX/ald-medium | [rmcpantoja/Ald_Mexican_Spanish_speech_dataset](https://huggingface.co/datasets/rmcpantoja/Ald_Mexican_Spanish_speech_dataset) | Unlicense |
| es_MX/claude-high | [HirCoir/Piper-TTS-Spanish](https://huggingface.co/spaces/HirCoir/Piper-TTS-Spanish) | Apache-2.0 |

Las demás voces del catálogo (`en_US`, `fr_FR`, etc.) tienen su licencia en su
`MODEL_CARD` dentro del mismo repositorio.

## Servicios externos (no incluidos)

- [LM Studio](https://lmstudio.ai/) y [Ollama](https://ollama.com/): servidores de LLM que el
  usuario ejecuta aparte. Cada modelo de lenguaje tiene su propia licencia.
