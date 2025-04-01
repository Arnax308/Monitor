import subprocess
import time
import webview
import sys
import os
import threading

def read_output(pipe, label):
    """Read and print output from the given pipe."""
    for line in iter(pipe.readline, ''):
        print(f"[{label}]: {line.strip()}")
        
def main():
    # Get base directory
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Set up paths
    log_path = os.path.join(base_dir, "launcher.log")
    
    # Create log file
    with open(log_path, "w") as log:
        log.write(f"Launcher started at {time.ctime()}\n")
        log.write(f"Base directory: {base_dir}\n")
    
    # Path to the document tracker script
    if getattr(sys, 'frozen', False):
        document_path = os.path.join(base_dir, "document_tracker.py")
    else:
        # When not frozen, use the original relative path
        document_path = os.path.join(base_dir, "../../document_tracker.py")
    
    with open(log_path, "a") as log:
        log.write(f"Document path: {document_path}\n")
        log.write(f"Path exists: {os.path.exists(document_path)}\n")
    
    # Use a fixed path for Python in frozen mode
    # This is hardcoded but necessary for now
    if getattr(sys, 'frozen', False):
        python_path = r"C:\Users\Arnav\AppData\Local\Programs\Python\Python313\python.exe"
    else:
        python_path = sys.executable
    
    with open(log_path, "a") as log:
        log.write(f"Python path: {python_path}\n")
    
    # Start Streamlit
    try:
        command = [
            python_path,
            "-m", "streamlit",
            "run", document_path,
            "--server.port", "8501",
            "--server.headless", "true",
            "--server.address", "127.0.0.1"
        ]
        
        with open(log_path, "a") as log:
            log.write(f"Command: {' '.join(command)}\n")
        
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=base_dir,
            universal_newlines=True
        )
        
        # Log process ID
        with open(log_path, "a") as log:
            log.write(f"Streamlit process ID: {proc.pid}\n")
        
        # Capture output
        threading.Thread(target=read_output, args=(proc.stdout, "STDOUT"), daemon=True).start()
        threading.Thread(target=read_output, args=(proc.stderr, "STDERR"), daemon=True).start()
        
        # Wait for server to start
        with open(log_path, "a") as log:
            log.write("Waiting for Streamlit server to start...\n")
        
        time.sleep(8)
        
        # Launch webview
        with open(log_path, "a") as log:
            log.write("Creating WebView window...\n")
        
        window = webview.create_window("Trace Document Tracker", "http://127.0.0.1:8501")
        webview.start()
        
        # Terminate Streamlit after window closes
        with open(log_path, "a") as log:
            log.write("WebView closed, terminating Streamlit...\n")
        
        proc.terminate()
        proc.wait(timeout=5)
        
    except Exception as e:
        with open(log_path, "a") as log:
            log.write(f"Error: {str(e)}\n")

if __name__ == "__main__":
    main()