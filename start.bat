@echo off
cd /d %~dp0
uv --cache-dir .uv-cache run --no-dev --env-file .env python -m backend.scripts.serve
pause
