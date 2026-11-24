@echo off
rem DeNotive Vectorizer: vectorizer.bat generate ^| preview ^| train ^| predict picture.png [--clipboard]
rem Uses the Python on PATH, which needs PyTorch, PySide6, numpy and Pillow.
python -c "import torch, PySide6, numpy, PIL" 2>nul
if errorlevel 1 (
    echo This needs Python with PyTorch, PySide6, numpy and Pillow:
    echo     python -m pip install torch PySide6-Essentials numpy pillow
    exit /b 1
)
python "%~dp0main.py" %*
