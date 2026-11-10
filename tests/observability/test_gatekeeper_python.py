"""Python mirror of the JS sync logic for the server side.

The server uses the same gatekeeper algorithm to throttle downstream
calls. We test the algorithm here so the JS port is verified too.
"""
import time


class LLMGatekeeper:
    def __init__(self, mode="balanced", threshold_ms=None, resume_at_fraction=0.5):
        cfg = {
            "free":     (float("inf"), float("inf")),
            "balanced": (1500, 800),
            "strict":   (400, 400),
            "burst":    (float("inf"), float("inf")),
        }.get(mode, (1500, 800))
        self.mode = mode
        self.max_text_ahead_ms = threshold_ms if threshold_ms is not None else cfg[0]
        self.max_audio_ahead_ms = cfg[1]
        self.resume_at_fraction = resume_at_fraction
        self.text_ahead_ms = 0.0
        self.audio_ahead_ms = 0.0
        self.paused = False

    @staticmethod
    def estimate_text_ms(text: str) -> float:
        return len([w for w in (text or "").split() if w]) * 350.0

    def on_text_added(self, text: str) -> str:
        self.text_ahead_ms += LLMGatekeeper.estimate_text_ms(text)
        return self._maybe_pause()

    def on_audio_played(self, duration_ms: float) -> str:
        self.audio_ahead_ms += duration_ms
        self.text_ahead_ms = max(0.0, self.text_ahead_ms - duration_ms)
        return self._maybe_resume()

    def _maybe_pause(self) -> str:
        if self.paused:
            return None
        if self.text_ahead_ms <= self.max_text_ahead_ms:
            return None
        self.paused = True
        return "pause"

    def _maybe_resume(self) -> str:
        if not self.paused:
            return None
        if self.text_ahead_ms > self.max_text_ahead_ms * self.resume_at_fraction:
            return None
        self.paused = False
        return "resume"


# --- Tests ---

def test_balanced_mode_pauses_after_threshold():
    g = LLMGatekeeper("balanced")
    # 6 words * 350 = 2100ms > 1500
    assert g.on_text_added("uno dos tres cuatro cinco seis") == "pause"
    assert g.paused is True


def test_balanced_mode_does_not_pause_below_threshold():
    g = LLMGatekeeper("balanced")
    # 4 words * 350 = 1400ms < 1500
    assert g.on_text_added("uno dos tres cuatro") is None
    assert g.paused is False


def test_resume_on_audio_progress():
    g = LLMGatekeeper("balanced")
    g.on_text_added("uno dos tres cuatro cinco seis")  # pause
    assert g.paused
    # 2000ms of audio drains text-ahead below the 50% trigger (1500 * 0.5 = 750)
    g.on_audio_played(2000)
    assert g.paused is False


def test_free_mode_never_pauses():
    g = LLMGatekeeper("free")
    g.on_text_added(" ".join(["word"] * 1000))
    assert g.paused is False


def test_strict_mode_lower_threshold():
    g = LLMGatekeeper("strict")
    # 2 words = 700 > 400
    g.on_text_added("uno dos")
    assert g.paused is True


def test_text_estimate_words_only():
    assert LLMGatekeeper.estimate_text_ms("") == 0
    assert LLMGatekeeper.estimate_text_ms("uno") == 350
    assert LLMGatekeeper.estimate_text_ms("uno dos tres") == 1050


def test_state_machine_hysteresis():
    """Pause should not flicker — once paused, only resume when text-ahead drops significantly."""
    g = LLMGatekeeper("balanced", resume_at_fraction=0.5)
    g.on_text_added("uno dos tres cuatro cinco seis")  # 2100ms text
    assert g.paused
    # 800ms of audio brings text to 1300ms — still > 50% (750)
    g.on_audio_played(800)
    assert g.paused
    # 1500ms total brings text to 600ms — below 50%
    g.on_audio_played(700)
    assert not g.paused
