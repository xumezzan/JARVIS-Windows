"""Local PCM16 energy endpointing; never starts capture or supplies task authority."""

import math
import os
import struct
from dataclasses import dataclass

FRAME_SAMPLES = 1600
FRAME_BYTES = FRAME_SAMPLES * 2


def rms_energy(pcm: bytes) -> float:
    if len(pcm) != FRAME_BYTES:
        raise ValueError("Expected one 100 ms mono PCM16 frame.")
    return math.sqrt(sum(value[0] ** 2 for value in struct.iter_unpack("<h", pcm)) / FRAME_SAMPLES)


@dataclass
class EnergyEndpoint:
    """Three voiced frames start a phrase; twelve quiet frames end that phrase."""

    threshold: float = 500.0
    calibrate: bool = False
    speech_started: bool = False
    voiced_frames: int = 0
    quiet_frames: int = 0
    noise_frames: int = 0
    noise_energy: float = 0.0

    @classmethod
    def configured(cls) -> "EnergyEndpoint":
        return cls(
            threshold=float(os.environ.get("JARVIS_VOICE_RMS_THRESHOLD", "500")),
            calibrate=os.environ.get("JARVIS_VOICE_RMS_CALIBRATE", "0") == "1",
        )

    def __post_init__(self) -> None:
        if not math.isfinite(self.threshold) or not 1 <= self.threshold <= 32768:
            raise ValueError("Invalid RMS threshold.")

    def feed(self, pcm: bytes) -> bool:
        energy = rms_energy(pcm)
        if self.calibrate and not self.speech_started and self.noise_frames < 2:
            if energy < self.threshold:
                self.voiced_frames = 0
                self.noise_frames += 1
                self.noise_energy += energy
                if self.noise_frames == 2:
                    self.threshold = min(32768.0, max(50.0, self.noise_energy / 2 * 3))
                return False
            self.noise_frames = 0
            self.noise_energy = 0.0
        if energy >= self.threshold:
            self.voiced_frames += 1
            self.quiet_frames = 0
            if self.voiced_frames >= 3:
                self.speech_started = True
        else:
            self.voiced_frames = 0
            if self.speech_started:
                self.quiet_frames += 1
        return self.speech_started and self.quiet_frames >= 12
