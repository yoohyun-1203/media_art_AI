@echo off
echo AI 모델 및 백엔드 서버를 시작합니다...
echo (첫 실행 시 모델 다운로드로 인해 시간이 약간 소요될 수 있습니다)

call .\venv311\Scripts\activate.bat
.\venv311\Scripts\python.exe main.py
pause
