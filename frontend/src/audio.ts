// Microphone capture (PCM16 mono, 16 kHz as Gemini expects, or 24 kHz for Azure Voice Live) and question playback
// (PCM16 mono 24 kHz, as Gemini produces).

const MIC_RATE = 16000
const SPEAKER_RATE = 24000

const CAPTURE_WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (channel) this.port.postMessage(channel.slice(0))
    return true
  }
}
registerProcessor('capture', Capture)
`

export class Microphone {
  private context: AudioContext | null = null
  private stream: MediaStream | null = null
  private buffer = new Float32Array(0)
  private position = 0 // fractional read position in `buffer`
  /** Chunks are only passed on while this is true (i.e. while the server is listening). */
  sending = false

  private onChunk: (pcm: ArrayBuffer) => void
  private onLevel: (level: number) => void
  private rate: number
  private chunkSamples: number // 100 ms

  constructor(onChunk: (pcm: ArrayBuffer) => void, onLevel: (level: number) => void, rate = MIC_RATE) {
    this.onChunk = onChunk
    this.onLevel = onLevel
    this.rate = rate
    this.chunkSamples = rate / 10
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    this.context = new AudioContext()
    const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: 'application/javascript' }))
    await this.context.audioWorklet.addModule(url)
    URL.revokeObjectURL(url)

    const source = this.context.createMediaStreamSource(this.stream)
    const capture = new AudioWorkletNode(this.context, 'capture')
    capture.port.onmessage = (event: MessageEvent<Float32Array>) => this.receive(event.data)
    source.connect(capture)
  }

  private receive(samples: Float32Array) {
    let sum = 0
    for (const s of samples) sum += s * s
    this.onLevel(Math.min(1, Math.sqrt(sum / samples.length) * 6))

    if (!this.sending || !this.context) {
      this.buffer = new Float32Array(0)
      this.position = 0
      return
    }

    const joined = new Float32Array(this.buffer.length + samples.length)
    joined.set(this.buffer)
    joined.set(samples, this.buffer.length)
    this.buffer = joined

    // linear resampling from the device rate (usually 48 kHz) to the wanted rate
    const step = this.context.sampleRate / this.rate
    const chunk = this.chunkSamples
    while (this.buffer.length - this.position >= chunk * step + 1) {
      const out = new Int16Array(chunk)
      for (let i = 0; i < chunk; i++) {
        const x = this.position + i * step
        const i0 = Math.floor(x)
        const value = this.buffer[i0] + (this.buffer[i0 + 1] - this.buffer[i0]) * (x - i0)
        out[i] = Math.max(-32768, Math.min(32767, Math.round(value * 32767)))
      }
      this.onChunk(out.buffer)
      this.position += chunk * step
      const consumed = Math.floor(this.position)
      this.buffer = this.buffer.slice(consumed)
      this.position -= consumed
    }
  }

  async stop() {
    this.sending = false
    this.stream?.getTracks().forEach((track) => track.stop())
    await this.context?.close()
    this.context = null
    this.stream = null
  }
}

export class QuestionPlayer {
  private context = new AudioContext()
  private nextStart = 0
  private playing = new Set<AudioBufferSourceNode>()

  /** Must be called from a click handler, so the browser allows audio. */
  async unlock() {
    await this.context.resume()
  }

  play(pcm: ArrayBuffer) {
    const ints = new Int16Array(pcm)
    const floats = new Float32Array(ints.length)
    for (let i = 0; i < ints.length; i++) floats[i] = ints[i] / 32768

    const buffer = this.context.createBuffer(1, floats.length, SPEAKER_RATE)
    buffer.copyToChannel(floats, 0)
    const source = this.context.createBufferSource()
    source.buffer = buffer
    source.connect(this.context.destination)
    this.playing.add(source)
    source.onended = () => this.playing.delete(source)

    // chunks are queued back to back so the question plays without gaps
    const start = Math.max(this.context.currentTime + 0.05, this.nextStart)
    source.start(start)
    this.nextStart = start + buffer.duration
  }

  /** True while queued audio is still playing. */
  get isPlaying() {
    return this.nextStart > this.context.currentTime
  }

  /** Drops everything queued (the patient interrupted). */
  clear() {
    for (const source of this.playing) source.stop()
    this.playing.clear()
    this.nextStart = 0
  }

  /** Resolves when everything queued so far has finished playing. */
  finished(): Promise<void> {
    const remaining = Math.max(0, this.nextStart - this.context.currentTime)
    return new Promise((resolve) => setTimeout(resolve, remaining * 1000 + 150))
  }

  async close() {
    await this.context.close()
  }
}
