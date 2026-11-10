// MicCapture — thin wrapper over getUserMedia + VAD. Captures audio
// at 16kHz mono, detects speech boundaries with a simple RMS VAD,
// and forwards the PCM Float32Array to the transport's sendAudioChunk.
//
// This is a minimal port of the existing AudioCapture class; the heavy
// DSP (denoise, resampling) lives in audio/dsp.js if needed.

const SAMPLE_RATE = 16000;
const VAD_THRESHOLD = 0.01;
const SILENCE_FRAMES_TO_END = 12;   // ~250ms at 30ms/frame

export class MicCapture {
  constructor(rootEl, transport) {
    this.root = rootEl;
    this.transport = transport;
    this.stream = null;
    this.audioContext = null;
    this.recording = false;
    this.frames = [];
    this.silentFrames = 0;
    this._render();
  }

  _render() {
    this.root.innerHTML = `
      <h2>Micrófono</h2>
      <button id="micToggle" class="btn btn-primary">Iniciar captura</button>
      <div class="status" id="micStatus" style="margin-top:8px;color:#888;">Inactivo</div>
    `;
    this.root.querySelector('#micToggle').addEventListener('click', () => this.toggle());
  }

  bind() {
    this.root.querySelector('#micToggle').addEventListener('click', () => this.toggle());
  }

  async toggle() {
    if (this.recording) {
      this.stop();
    } else {
      await this.start();
    }
  }

  async start() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: SAMPLE_RATE } });
    } catch (err) {
      this._setStatus('Permiso denegado: ' + err.message);
      return;
    }
    this.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
    const source = this.audioContext.createMediaStreamSource(this.stream);
    const processor = this.audioContext.createScriptProcessor(2048, 1, 1);
    source.connect(processor);
    processor.connect(this.audioContext.destination);
    this.frames = [];
    this.silentFrames = 0;
    this.recording = true;
    processor.onaudioprocess = (e) => this._onFrame(e);
    this._setStatus('Escuchando...');
    this.root.querySelector('#micToggle').textContent = 'Detener';
  }

  _onFrame(e) {
    if (!this.recording) return;
    const data = e.inputBuffer.getChannelData(0);
    this.frames.push(new Float32Array(data));
    // Simple RMS VAD.
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length);
    if (rms < VAD_THRESHOLD) {
      this.silentFrames++;
      if (this.silentFrames >= SILENCE_FRAMES_TO_END && this.frames.length > 4) {
        this._flush();
      }
    } else {
      this.silentFrames = 0;
    }
  }

  async _flush() {
    if (!this.frames.length) return;
    const total = this.frames.reduce((n, f) => n + f.length, 0);
    const merged = new Float32Array(total);
    let off = 0;
    for (const f of this.frames) { merged.set(f, off); off += f.length; }
    this.frames = [];
    this.silentFrames = 0;
    this._setStatus('Procesando...');
    try {
      await this.transport.sendAudioChunk(merged);
      this._setStatus('Listo');
    } catch (err) {
      this._setStatus('Error: ' + err.message);
    }
  }

  stop() {
    this.recording = false;
    if (this.stream) this.stream.getTracks().forEach(t => t.stop());
    if (this.audioContext) this.audioContext.close();
    this._flush();
    this._setStatus('Inactivo');
    this.root.querySelector('#micToggle').textContent = 'Iniciar captura';
  }

  _setStatus(text) {
    const el = this.root.querySelector('#micStatus');
    if (el) el.textContent = text;
  }
}
