import numpy as np
import torch
from torch.nn.functional import pad
import time
from queue import Queue
import sounddevice as sd
from .config import settings

CHUNK = settings.CHUNK
CHANNELS = settings.CHANNELS
RATE = settings.RATE
SILENCE_THRESHOLD = settings.SILENCE_THRESHOLD
SPEECH_CHECK_THRESHOLD = settings.SPEECH_CHECK_THRESHOLD
MAX_SILENCE_DURATION = settings.MAX_SILENCE_DURATION

_audio_recording = []
_recording_active = False


def record_audio(duration=None):
    if duration is None:
        duration = settings.RECORD_DURATION

    print("\nRecording...")
    audio_data = sd.rec(
        int(duration * RATE), samplerate=RATE, channels=CHANNELS, dtype="float32"
    )
    sd.wait()
    print("Done recording")
    return audio_data.flatten()


def record_continuous_audio():
    global _audio_recording, _recording_active
    _audio_recording = []
    _recording_active = True

    def audio_callback(indata, frames, time, status):
        global _audio_recording, _recording_active
        if status:
            print(f"Input status: {status}")
        audio_chunk = indata.flatten()
        _audio_recording.append(audio_chunk.copy())

    buffer_frames = []
    silence_frames = 0
    max_silence_frames = int(RATE / CHUNK * 1)
    recording = False
    frames = []

    print("\nListening... (Press Ctrl+C to stop)")

    try:
        with sd.InputStream(
            channels=CHANNELS,
            samplerate=RATE,
            blocksize=CHUNK,
            dtype="float32",
            callback=audio_callback,
        ):
            while _recording_active:
                time.sleep(0.01)
                if len(_audio_recording) > 0:
                    audio_chunk = _audio_recording[-1]
                    buffer_frames.append(audio_chunk)

                    if len(buffer_frames) > int(RATE * 0.5 / CHUNK):
                        buffer_frames.pop(0)

                    audio_level = np.abs(np.concatenate(buffer_frames)).mean()

                    if audio_level > SILENCE_THRESHOLD:
                        if not recording:
                            print("\nPotential speech detected...")
                            recording = True
                            frames.extend(buffer_frames)
                        frames.append(audio_chunk)
                        silence_frames = 0
                    elif recording:
                        frames.append(audio_chunk)
                        silence_frames += 1

                        if silence_frames >= max_silence_frames:
                            print("Processing speech segment...")
                            _recording_active = False
                            break
    except KeyboardInterrupt:
        pass

    if frames:
        return np.concatenate(frames)
    return None


def check_for_speech(timeout=0.1):
    frames = []
    is_speech = False

    def audio_callback(indata, frames_count, time, status):
        if status:
            print(f"Input status: {status}")
        frames.append(indata.flatten())

    try:
        with sd.InputStream(
            channels=CHANNELS,
            samplerate=RATE,
            blocksize=CHUNK,
            dtype="float32",
            callback=audio_callback,
        ):
            sd.sleep(int(timeout * 1000))
            for audio_chunk in frames:
                audio_level = np.abs(audio_chunk).mean()
                if audio_level > SPEECH_CHECK_THRESHOLD:
                    is_speech = True
                    break
    except Exception as e:
        print(f"Error checking for speech: {e}")

    if is_speech and frames:
        return True, np.concatenate(frames)
    return False, None


def play_audio_with_interrupt(audio_data, sample_rate=24000):
    interrupt_queue = Queue()

    def input_callback(indata, frames, time, status):
        if status:
            print(f"Input status: {status}")
        audio_level = np.abs(indata[:, 0]).mean()
        if audio_level > settings.INTERRUPTION_THRESHOLD:
            interrupt_queue.put(True)

    def output_callback(outdata, frames, time, status):
        if status:
            print(f"Output status: {status}")
        if not interrupt_queue.empty():
            raise sd.CallbackStop()

        remaining = len(audio_data) - output_callback.position
        if remaining == 0:
            raise sd.CallbackStop()
        valid_frames = min(remaining, frames)
        outdata[:valid_frames, 0] = audio_data[
            output_callback.position : output_callback.position + valid_frames
        ]
        if valid_frames < frames:
            outdata[valid_frames:] = 0
        output_callback.position += valid_frames

    output_callback.position = 0

    try:
        with sd.InputStream(
            channels=1, callback=input_callback, samplerate=settings.RATE
        ):
            with sd.OutputStream(
                channels=1, callback=output_callback, samplerate=sample_rate
            ):
                while output_callback.position < len(audio_data):
                    sd.sleep(100)
                    if not interrupt_queue.empty():
                        return True, None
        return False, None
    except sd.CallbackStop:
        return True, None
    except Exception as e:
        print(f"Error during playback: {str(e)}")
        return False, None


def init_vad_pipeline(hf_token):
    from pyannote.audio import Model
    from pyannote.audio.pipelines import VoiceActivityDetection

    model = Model.from_pretrained(settings.VAD_MODEL, use_auth_token=hf_token)
    pipeline = VoiceActivityDetection(segmentation=model)

    HYPER_PARAMETERS = {
        "min_duration_on": settings.VAD_MIN_DURATION_ON,
        "min_duration_off": settings.VAD_MIN_DURATION_OFF,
    }
    pipeline.instantiate(HYPER_PARAMETERS)

    return pipeline


def detect_speech_segments(pipeline, audio_data, sample_rate=None):
    if sample_rate is None:
        sample_rate = settings.RATE

    if len(audio_data.shape) == 1:
        audio_data = audio_data.reshape(1, -1)

    if not isinstance(audio_data, torch.Tensor):
        audio_data = torch.from_numpy(audio_data)

    if audio_data.shape[1] < sample_rate:
        padding_size = sample_rate - audio_data.shape[1]
        audio_data = pad(audio_data, (0, padding_size))

    vad = pipeline({"waveform": audio_data, "sample_rate": sample_rate})

    speech_segments = []
    for speech in vad.get_timeline().support():
        start_sample = int(speech.start * sample_rate)
        end_sample = int(speech.end * sample_rate)
        if start_sample < audio_data.shape[1]:
            end_sample = min(end_sample, audio_data.shape[1])
            segment = audio_data[0, start_sample:end_sample]
            speech_segments.append(segment)

    if speech_segments:
        return torch.cat(speech_segments)
    return None


def transcribe_audio(processor, model, audio_data, sampling_rate=None):
    if sampling_rate is None:
        sampling_rate = settings.RATE

    if audio_data is None:
        return ""

    if isinstance(audio_data, torch.Tensor):
        audio_data = audio_data.numpy()

    input_features = processor(
        audio_data, sampling_rate=sampling_rate, return_tensors="pt"
    ).input_features
    predicted_ids = model.generate(input_features)
    transcription = processor.batch_decode(predicted_ids, skip_special_tokens=True)
    return transcription[0]
