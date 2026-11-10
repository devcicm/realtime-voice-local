"""Chunking incremental del stream del LLM para TTS de baja latencia.

El primer fragmento se corta en la primera pausa natural (coma, punto y
coma, dos puntos) en cuanto hay `first_min_words` palabras: asi el TTS
arranca con 3-6 palabras en vez de esperar la frase entera. Los
siguientes se cortan por fin de frase, o por pausa si ya llevan
max_words/2 palabras, o en un espacio al llegar a max_words.

Solo se corta cuando ya llego el caracter siguiente y es espacio, para
no partir "3.5" ni "Sr.Perez".
"""
from __future__ import annotations

import re

SENTENCE_END = ".!?…"
CLAUSE_END = ",;:"
_WS = re.compile(r"\s+")
# Emojis / pictogramas: Piper los deletrea o los convierte en ruido.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]+")
_SPACE_PUNCT = re.compile(r"\s+([.,;:!?…])")


class ClauseChunker:
    def __init__(self, first_min_words: int = 2, max_words: int = 28) -> None:
        self.first_min_words = first_min_words
        self.max_words = max_words
        self._buf = ""
        self._emitted = 0

    def push(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            piece = _clean(self._buf[:cut])
            self._buf = self._buf[cut:].lstrip()
            if piece:
                out.append(piece)
                self._emitted += 1
        return out

    def flush(self) -> list[str]:
        piece = _clean(self._buf)
        self._buf = ""
        if piece:
            self._emitted += 1
            return [piece]
        return []

    def _find_cut(self):
        buf = self._buf
        first = self._emitted == 0
        clause_min = self.first_min_words if first else max(self.first_min_words, self.max_words // 2)
        words = 0
        in_word = False
        last_space_cut = None
        for i, ch in enumerate(buf):
            if ch.isspace():
                if in_word:
                    words += 1
                    in_word = False
                    last_space_cut = i
                continue
            in_word = True
            nxt = buf[i + 1] if i + 1 < len(buf) else None
            if nxt is None or not nxt.isspace():
                continue
            wc = words + 1
            if ch in SENTENCE_END:
                return i + 1
            if ch in CLAUSE_END and wc >= clause_min:
                return i + 1
        if words >= self.max_words and last_space_cut is not None:
            return last_space_cut
        return None


def _clean(text: str) -> str:
    text = _EMOJI.sub("", text.replace("*", "").replace("#", ""))
    return _SPACE_PUNCT.sub(lambda m: m.group(1), _WS.sub(" ", text)).strip()


def heard_text(segments: list[tuple[str, float, float]], played_ms: float) -> str:
    """Texto realmente escuchado dado `played_ms`.

    `segments` = [(texto, start_ms, duration_ms), ...] en la linea de tiempo
    de audio de la respuesta. Dentro del segmento en curso se estima el
    corte por palabras ponderando por longitud (misma heuristica que
    `_estimate_word_offsets` del servidor TTS).
    """
    parts: list[str] = []
    for text, start, dur in segments:
        if played_ms >= start + dur:
            parts.append(text)
            continue
        if played_ms <= start or dur <= 0:
            break
        words = text.split()
        weights = [max(1, len(w)) for w in words]
        total = sum(weights) or 1
        frac = (played_ms - start) / dur
        acc = 0.0
        kept = []
        for w, wt in zip(words, weights):
            if acc / total >= frac:
                break
            kept.append(w)
            acc += wt
        if kept:
            parts.append(" ".join(kept))
        break
    return " ".join(parts).strip()
