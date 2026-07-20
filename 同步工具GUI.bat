@echo off
chcp 65001 >nul
title 雲端圖資同步工具（GUI）
cd /d "%~dp0"
python drive_sync_gui.py
