"""Phone-call audio to and from the voice agent's audio (PCM16 mono 24 kHz), in plain Python.

A phone line carries 8 kHz audio, either 16-bit PCM or G.711 mu-law (Twilio). Exotel can send
24 kHz PCM, which needs no conversion at all.
"""
import sys
from array import array

AGENT_RATE = 24000
_BIAS, _CLIP = 0x84, 32635


def _decode_ulaw(byte):
    byte = ~byte & 0xFF
    sample = (((byte & 0x0F) << 3) + _BIAS) << ((byte & 0x70) >> 4)
    return (_BIAS - sample) if byte & 0x80 else (sample - _BIAS)


_ULAW_TO_PCM = [_decode_ulaw(i) for i in range(256)]


def _encode_ulaw(sample):
    sign = 0x80 if sample < 0 else 0
    sample = min(abs(sample), _CLIP) + _BIAS
    exponent = sample.bit_length() - 8
    return ~(sign | (exponent << 4) | ((sample >> (exponent + 3)) & 0x0F)) & 0xFF


_PCM_TO_ULAW = bytes(_encode_ulaw((i << 2) - 32768) for i in range(16384))   # indexed by (sample + 32768) >> 2


def ulaw_to_pcm(data):
    return array("h", [_ULAW_TO_PCM[b] for b in data])


def pcm_to_ulaw(samples):
    return bytes(_PCM_TO_ULAW[(s + 32768) >> 2] for s in samples)


def to_array(pcm_bytes):
    """16-bit little-endian PCM bytes to samples (an odd trailing byte is dropped)."""
    samples = array("h")
    samples.frombytes(pcm_bytes[: len(pcm_bytes) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def to_bytes(samples):
    if sys.byteorder == "big":
        samples = array("h", samples)
        samples.byteswap()
    return samples.tobytes()


class Upsampler:
    """8 kHz to 24 kHz by linear interpolation (three samples out for each one in), continuing across chunks."""

    def __init__(self):
        self.last = 0

    def feed(self, samples):
        out = array("h")
        previous = self.last
        for s in samples:
            out.append(previous + (s - previous) // 3)
            out.append(previous + (s - previous) * 2 // 3)
            out.append(s)
            previous = s
        self.last = previous
        return out


class Downsampler:
    """24 kHz to 8 kHz: each output sample is the mean of three inputs (a light low-pass), continuing across chunks."""

    def __init__(self):
        self.rest = array("h")

    def feed(self, samples):
        samples = self.rest + samples
        whole = len(samples) // 3 * 3
        self.rest = samples[whole:]
        return array("h", [(samples[i] + samples[i + 1] + samples[i + 2]) // 3 for i in range(0, whole, 3)])


class Codec:
    """Converts between the phone provider's audio and the agent's audio.

    kind "mulaw8k" (Twilio), "pcm8k" (Exotel's default) or "pcm24k" (Exotel with sample-rate=24000).
    """

    def __init__(self, kind):
        if kind not in ("mulaw8k", "pcm8k", "pcm24k"):
            raise ValueError(f"unsupported phone audio {kind}")
        self.kind = kind
        self._up, self._down = Upsampler(), Downsampler()

    def from_phone(self, data):
        """The caller's audio -> PCM16 24 kHz bytes for the agent."""
        if self.kind == "pcm24k":
            return data
        samples = ulaw_to_pcm(data) if self.kind == "mulaw8k" else to_array(data)
        return to_bytes(self._up.feed(samples))

    def to_phone(self, data):
        """The agent's PCM16 24 kHz bytes -> audio for the caller."""
        if self.kind == "pcm24k":
            return data
        samples = self._down.feed(to_array(data))
        return pcm_to_ulaw(samples) if self.kind == "mulaw8k" else to_bytes(samples)

    @property
    def bytes_per_second(self):
        return {"mulaw8k": 8000, "pcm8k": 16000, "pcm24k": 48000}[self.kind]
