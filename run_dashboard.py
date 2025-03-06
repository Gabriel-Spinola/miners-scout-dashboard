import subprocess
import os
import sys

# Obter o diretório do script
if getattr(sys, 'frozen', False):
    # Estamos em um executável criado pelo PyInstaller
    app_path = os.path.dirname(sys.executable)
else:
    # Estamos em um script normal
    app_path = os.path.dirname(os.path.abspath(__file__))

# Comando para executar o Streamlit
cmd = [
    "streamlit", "run",
    os.path.join(app_path, "dashboard.py"),
    "--server.port=8501",
    "--server.headless=true",
    "--browser.serverAddress=localhost",
    "--browser.gatherUsageStats=false"
]

# Executar o comando
subprocess.run(cmd)