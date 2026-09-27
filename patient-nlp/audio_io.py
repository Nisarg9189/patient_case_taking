"""Where an interview's audio comes from and goes to.

LocalAudio   - this machine's microphone and speakers (python3 workflow.py)
BrowserAudio - a browser connected over a WebSocket (backend/app.py)

The voice agent looks up the audio for an interview with audio_for(session_id); sessions
without a registered BrowserAudio use the shared LocalAudio.

Audio format: microphone PCM16 mono 16 kHz in, Gemini's voice PCM16 mono 24 kHz out.
"""
import asyncio

MIC_DEVICE = 1
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1
BLOCK_SIZE = 1600

STOP_TIMEOUT_SECONDS = 3

# how long to wait for a browser to report that the question finished playing
PLAYBACK_WAIT_SECONDS = 60


async def close_audio_stream(stream, name):
    """abort() + close() a sounddevice stream off the event loop, giving up after a timeout."""

    def close():
        stream.abort()
        stream.close()

    task = asyncio.ensure_future(asyncio.to_thread(close))
    done, _ = await asyncio.wait({task}, timeout=STOP_TIMEOUT_SECONDS)

    if not done:
        print(f"\n⚠️ {name} did not close within {STOP_TIMEOUT_SECONDS}s; continuing", flush=True)
    elif task.exception():
        print(f"\n⚠️ {name} close failed: {task.exception()!r}", flush=True)


class LocalAudio:
    """This machine's microphone and speakers.

    One microphone stream stays open for the whole interview (opened on first use). Audio
    is only forwarded while an answer is being listened for, so Gemini's own voice and
    anything said between questions never reaches Gemini as patient speech.
    """

    def __init__(self):
        self._microphone = None
        self._listener = None  # (event loop, on_audio) while listening

    # ---- microphone

    def _callback(self, indata, frames, time_info, status):
        # runs on the audio driver's thread
        listener = self._listener
        if listener is None:
            return
        loop, on_audio = listener
        loop.call_soon_threadsafe(on_audio, indata.copy().tobytes(), status)

    def _open_microphone(self):
        import sounddevice as sd

        if self._microphone is not None and self._microphone.active:
            return
        if self._microphone is not None:
            try:
                self._microphone.close()
            except Exception:
                pass
        self._microphone = sd.InputStream(
            device=MIC_DEVICE,
            samplerate=INPUT_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=BLOCK_SIZE,
            callback=self._callback,
        )
        self._microphone.start()
        print(f"\n🎤 Microphone opened for the interview (device {MIC_DEVICE})", flush=True)

    def start_listening(self, loop, on_audio):
        """Forward microphone audio to on_audio(bytes, status) on `loop` until stop_listening()."""
        self._open_microphone()
        self._listener = (loop, on_audio)

    def stop_listening(self):
        self._listener = None

    async def close(self):
        self._listener = None
        microphone, self._microphone = self._microphone, None
        if microphone is not None:
            await close_audio_stream(microphone, "Microphone")

    # ---- speaker

    def open_speaker(self):
        import sounddevice as sd

        speaker = sd.RawOutputStream(samplerate=OUTPUT_SAMPLE_RATE, channels=1, dtype="int16")
        speaker.start()
        return speaker

    async def close_speaker(self, speaker):
        await close_audio_stream(speaker, "Speaker")

    async def question_finished(self):
        # speaker.write() blocks while playing, so the question has already been heard
        pass

    # ---- events (the terminal already shows everything)

    def emit(self, event):
        pass


class _BrowserSpeaker:
    def __init__(self, audio):
        self.audio = audio

    def write(self, pcm):
        self.audio._outgoing.put_nowait(bytes(pcm))


class BrowserAudio:
    """A browser on the other end of a WebSocket.

    Outgoing audio (bytes) and events (dicts) go through one queue so they reach the
    browser in order; `send(item)` is the coroutine that writes one item to the socket.
    The backend passes microphone audio in with feed_microphone() and calls
    playback_done() when the browser reports that the question finished playing.
    """

    def __init__(self, send):
        self._outgoing = asyncio.Queue()
        self._listener = None
        self._played = asyncio.Event()
        self._sender = asyncio.create_task(self._send_all(send))

    async def _send_all(self, send):
        connected = True
        while True:
            item = await self._outgoing.get()
            try:
                if connected:
                    await send(item)
            except Exception:
                connected = False  # socket gone: keep draining so flush() does not hang
            finally:
                self._outgoing.task_done()

    async def flush(self, timeout=3):
        """Wait until everything queued so far has been sent (e.g. the final "done" event)."""
        try:
            await asyncio.wait_for(self._outgoing.join(), timeout)
        except asyncio.TimeoutError:
            pass

    # ---- microphone

    def start_listening(self, loop, on_audio):
        self._listener = on_audio
        self.emit({"type": "listening"})

    def stop_listening(self):
        if self._listener is not None:
            self._listener = None
            self.emit({"type": "stopped_listening"})

    def feed_microphone(self, pcm):
        listener = self._listener
        if listener is not None:
            listener(pcm, None)

    # ---- speaker

    def open_speaker(self):
        return _BrowserSpeaker(self)

    async def close_speaker(self, speaker):
        pass

    async def question_finished(self):
        """Wait until the browser has played the whole question, so listening does not
        start while the question is still coming out of the patient's speakers."""
        self._played.clear()
        self.emit({"type": "question_audio_end"})
        try:
            await asyncio.wait_for(self._played.wait(), timeout=PLAYBACK_WAIT_SECONDS)
        except asyncio.TimeoutError:
            print("\n⚠️ Browser did not report the end of playback; listening anyway", flush=True)

    def playback_done(self):
        self._played.set()

    # ---- events

    def emit(self, event):
        self._outgoing.put_nowait(event)

    async def close(self):
        self._listener = None
        self._sender.cancel()


_local_audio = LocalAudio()
_browser_audio: dict[str, BrowserAudio] = {}


def register_audio(session_id, audio):
    _browser_audio[session_id] = audio


def unregister_audio(session_id):
    _browser_audio.pop(session_id, None)


def audio_for(session_id):
    return _browser_audio.get(session_id, _local_audio)


async def close_local_audio():
    """Stop this machine's microphone; call once when python3 workflow.py shuts down."""
    await _local_audio.close()
