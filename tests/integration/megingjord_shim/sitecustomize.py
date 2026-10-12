"""Test only: resolve MEGINGJORD's name to the integration stage's fake server.

Loaded by the API process the publication tests start, through PYTHONPATH and
nowhere else. `megingjord.veldris.internal` is the only MEGINGJORD host the
egress allow list admits, and a test machine does not resolve it; this answers
that one name with `DRAUPNIR_TEST_MEGINGJORD_ADDRESS` and every other name as
the system would. The TLS certificate is still checked against the name.
"""

import os
import socket

_ADDRESS = os.environ.get("DRAUPNIR_TEST_MEGINGJORD_ADDRESS", "")

if _ADDRESS:
    _resolve = socket.getaddrinfo

    def _getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host == "megingjord.veldris.internal":
            host = _ADDRESS
        return _resolve(host, *args, **kwargs)

    socket.getaddrinfo = _getaddrinfo
