@echo off
cd /d "d:\вайбкодинг\напоминание о парах"
".venv\Scripts\python.exe" -m bot.main >> logs\bot.log 2>&1
