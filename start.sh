#!/bin/bash
# Startar appen. Client ID sparas i appens inställningar (tangent s) eller frågas första gången.
cd "$(dirname "$0")"
[ -d .venv ] && source .venv/bin/activate
python3 app.py "$@"
