"""One line out of a helper, ended the way the parent reads it.

`print` writes through text mode, and on Windows text mode turns "\\n" into "\\r\\n". The
parent reads a line and compares the recorder's handshake byte for byte, so that invisible
carriage return was enough to make the microphone never announce itself: the indicator
stayed on "starting", and the recorder mistook the handshake for the answer.

Writing through the binary buffer sidesteps translation entirely. The parent is tolerant of
either ending as well - a protocol that depends on which platform a helper runs on is a
protocol that will break again.
"""

import sys


def emit(payload: str) -> None:
    sys.stdout.buffer.write(payload.encode() + b"\n")
    sys.stdout.buffer.flush()
