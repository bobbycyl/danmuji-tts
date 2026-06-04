#!/bin/bash
source .venv/bin/activate
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
python bili_chat_tts.py
