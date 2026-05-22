#!/usr/bin/env bash
# exit on error
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# Run database migrations or initial data loading if needed
# python scripts/setup_demo_data.py
