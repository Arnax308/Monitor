import subprocess
import time
import webview
import sys
import os
import signal
import runpy

def start_streamlit():
    command = [
        sys.executable,
        "--child",
        "-m", "streamlit",
        "run", "document_tracker.py",
        "--server.port", "8501"
    ]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc

def main():
    if "--child" in sys.argv:
        sys.argv.remove("--child")
        runpy.run_module("streamlit", run_name="__main__", alter_sys=True)
        return

    # Launch the Streamlit process in the background.
    streamlit_proc = start_streamlit()
    
    # Wait briefly to let the Streamlit server start.
    time.sleep(5)
    
    # Open the app in a pywebview window.
    window = webview.create_window("Trace", "http://localhost:8501")
    webview.start()
    
    # When the window is closed, terminate the Streamlit process.
    if os.name == 'nt':
        streamlit_proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        streamlit_proc.terminate()
    
if __name__ == '__main__':
    main()
