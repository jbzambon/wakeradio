#!/usr/bin/env python3
"""Send one call to Rdio Scanner exactly the way SDRTrunk does.

Mirrors SDRTrunk's RdioScannerBuilder byte for byte (same boundary, same part
order, audio part with no Content-Type), so a 200 "Call imported successfully."
here means SDRTrunk's uploads will be accepted too.

  fake-sdrtrunk-upload.py <url> <api-key> <audio.mp3> [system-id] [--test]

<url> is the full upload address, e.g.
https://wakeradio.joezambon.com/api/call-upload
--test sends SDRTrunk's connection test instead of a call.
"""

import sys
import time
import urllib.error
import urllib.request

BOUNDARY = "--sdrtrunk-sdrtrunk-sdrtrunk"


def part(name, value):
    return (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n").encode()


def main():
    args = [a for a in sys.argv[1:] if a != "--test"]
    test = "--test" in sys.argv
    if len(args) < 3:
        sys.exit(__doc__)
    url, key, audio = args[0], args[1], args[2]
    system = args[3] if len(args) > 3 else "1"

    if test:
        body = part("key", key) + part("system", system) + part("test", 1)
    else:
        with open(audio, "rb") as f:
            audio_bytes = f.read()
        body = part("key", key) + part("system", system)
        # SDRTrunk emits string parts first, then the file part, then closes.
        for name, value in [
            ("dateTime", int(time.time())),
            ("talkgroup", "1001"),
            ("source", "1234567"),
            ("frequency", "853962500"),
            ("talkgroupLabel", "TEST TG"),
            ("talkgroupGroup", "Test"),
            ("systemLabel", "Wake Test"),
        ]:
            body += part(name, value)
        body += (f"--{BOUNDARY}\r\nContent-Disposition: form-data; "
                 f"filename=\"20260923_010203TEST.mp3\"; name=\"audio\"\r\n\r\n").encode()
        body += audio_bytes

    body += f"\r\n--{BOUNDARY}--\r\n".encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={BOUNDARY}")
    req.add_header("User-Agent", "sdrtrunk")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(r.status, r.read().decode(errors="replace").strip())
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode(errors="replace").strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
