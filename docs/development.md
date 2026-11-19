# Development

voxprint-common is the shared library for the VOXPrint AI apps. It is a work in progress and is not meant for end users.

## Python

Python 3.14 or newer. CI tests 3.14 and 3.15. The newest stable target is 3.15. The floor stays 3.14 so Clarity and Enlarge can keep `onnxruntime-gpu` until a 3.15 wheel ships.

## Setup from a clone

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev,ui]"
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

More background: [README-full.md](README-full.md) and the other pages in this folder.

## Support the project

Donations (TRON TRC-20, USDT or TRX): `TYveZBXaSpM4FEHa6iGrc7A6zuLfS4z3x6`
Send only via the TRON (TRC-20) network; other networks lose funds permanently.
