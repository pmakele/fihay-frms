#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 -m pip install --retries 10 --timeout 120 -r requirements.txt
python3 run_fihay.py
