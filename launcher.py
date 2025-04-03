import subprocess
import time
import webview
import sys
import os
import threading
import shutil
import ctypes
from pathlib import Path
import json

def read_output(pipe, label):
    """Read and print output from the given pipe."""
    for line in iter(pipe.readline, ''):
        print(f"[{label}]: {line.strip()}")

def get_app_data_dir():
    """Get the proper application data directory for the app."""
    app_name = "TraceDocumentTracker"
    if os.name == 'nt':
        app_data = os.path.join(os.environ.get('LOCALAPPDATA', ''), app_name)
    elif os.name == 'posix':
        app_data = os.path.join(os.path.expanduser('~'), '.local', 'share', app_name)
    elif os.name == 'darwin':
        app_data = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', app_name)
    else:
        app_data = os.path.join(os.path.dirname(sys.executable), 'AppData')
    os.makedirs(app_data, exist_ok=True)
    return app_data

def ensure_app_files(app_data_dir, base_dir):
    """Ensure all necessary application files are in the app data directory."""
    log_path = os.path.join(app_data_dir, "launcher.log")
    with open(log_path, "a") as log:
        log.write(f"Ensuring application files in: {app_data_dir}\n")
    required_files = ["document_tracker.py", "entries.json"]
    search_locations = [base_dir, os.path.dirname(base_dir), os.getcwd()]
    program_files = os.environ.get('PROGRAMFILES', '')
    if program_files:
        install_dir = os.path.join(program_files, "TraceDocumentTracker")
        search_locations.append(install_dir)
    found_files = {}
    for file in required_files:
        if os.path.exists(os.path.join(app_data_dir, file)):
            found_files[file] = os.path.join(app_data_dir, file)
            with open(log_path, "a") as log:
                log.write(f"Found {file} already in app data directory\n")
            continue
        for location in search_locations:
            file_path = os.path.join(location, file)
            if os.path.exists(file_path):
                dest_path = os.path.join(app_data_dir, file)
                shutil.copy2(file_path, dest_path)
                found_files[file] = dest_path
                with open(log_path, "a") as log:
                    log.write(f"Copied {file} from {file_path} to {dest_path}\n")
                break
        if file not in found_files:
            if file == "entries.json":
                default_entries = {"documents": []}
                with open(os.path.join(app_data_dir, file), 'w') as f:
                    json.dump(default_entries, f)
                found_files[file] = os.path.join(app_data_dir, file)
                with open(log_path, "a") as log:
                    log.write(f"Created default {file} in app data directory\n")
    with open(log_path, "a") as log:
        log.write(f"Files found: {found_files}\n")
    return found_files

def find_python_executable():
    """Find a valid Python executable."""
    try:
        result = subprocess.run(["python", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            return "python"
    except FileNotFoundError:
        pass
    possible_paths = [
        r"C:\Python313\python.exe",
        r"C:\Program Files\Python\Python313\python.exe",
        r"C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python313\python.exe",
        r"C:\Python312\python.exe",
        r"C:\Program Files\Python\Python312\python.exe",
        r"C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Program Files\Python\Python311\python.exe",
        r"C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe",
    ]
    username = os.environ.get('USERNAME', '')
    possible_paths = [p.replace('%USERNAME%', username) for p in possible_paths]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return None

def check_streamlit_installed(python_path, log_path):
    """Check if streamlit is installed and install if needed."""
    try:
        result = subprocess.run([python_path, "-m", "streamlit", "--version"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            with open(log_path, "a") as log:
                log.write(f"Streamlit is installed: {result.stdout.strip()}\n")
            return True
        else:
            with open(log_path, "a") as log:
                log.write(f"Streamlit not found, attempting to install...\n")
            install_result = subprocess.run([python_path, "-m", "pip", "install", "streamlit"],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if install_result.returncode == 0:
                with open(log_path, "a") as log:
                    log.write(f"Streamlit installed successfully\n")
                return True
            else:
                with open(log_path, "a") as log:
                    log.write(f"Streamlit installation failed: {install_result.stderr}\n")
                return False
    except Exception as e:
        with open(log_path, "a") as log:
            log.write(f"Error checking/installing streamlit: {str(e)}\n")
        return False

def set_window_icon(base_dir, log_path, window):
    """Set the window icon on Windows using ctypes."""
    try:
        icon_path = os.path.join(base_dir, "trace_icon.ico")
        with open(log_path, "a") as log:
            log.write(f"Setting window icon from: {icon_path}\n")
        # Constants for Windows API
        WM_SETICON = 0x80
        ICON_SMALL = 0
        ICON_BIG = 1
        # Load the icon (LR_LOADFROMFILE flag = 0x00000010)
        hicon = ctypes.windll.user32.LoadImageW(0, icon_path, 1, 0, 0, 0x00000010)
        if hicon == 0:
            with open(log_path, "a") as log:
                log.write("Failed to load icon using LoadImageW.\n")
            return
        # Retrieve the native window handle from the webview window object.
        # This assumes that the webview backend provides a get_window_handle() method.
        hwnd = None
        if hasattr(window, 'gui') and hasattr(window.gui, 'get_window_handle'):
            hwnd = window.gui.get_window_handle()
        if hwnd:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
            with open(log_path, "a") as log:
                log.write("Window icon set successfully.\n")
        else:
            with open(log_path, "a") as log:
                log.write("Unable to retrieve native window handle; icon not set.\n")
    except Exception as e:
        with open(log_path, "a") as log:
            log.write(f"Error setting window icon: {str(e)}\n")

def main():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    app_data_dir = get_app_data_dir()
    log_path = os.path.join(app_data_dir, "launcher.log")
    with open(log_path, "w") as log:
        log.write(f"Launcher started at {time.ctime()}\n")
        log.write(f"Base directory: {base_dir}\n")
        log.write(f"App data directory: {app_data_dir}\n")
    found_files = ensure_app_files(app_data_dir, base_dir)
    document_path = found_files.get("document_tracker.py")
    if not document_path:
        with open(log_path, "a") as log:
            log.write("Critical error: document_tracker.py not found\n")
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(0, "Cannot find document_tracker.py. The application cannot start.", "Error", 0x10)
        return
    python_path = find_python_executable()
    if not python_path:
        with open(log_path, "a") as log:
            log.write("Critical error: Python not found\n")
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(0, "Python is not installed or cannot be found. Please install Python 3.11 or later.", "Error", 0x10)
        return
    with open(log_path, "a") as log:
        log.write(f"Python path: {python_path}\n")
    if not check_streamlit_installed(python_path, log_path):
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(0, "Streamlit installation failed. Please install Streamlit manually using pip.", "Error", 0x10)
        return
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
        with open(log_path, "a") as log:
            log.write(f"Working directory: {app_data_dir}\n")
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=app_data_dir,
            universal_newlines=True
        )
        with open(log_path, "a") as log:
            log.write(f"Streamlit process ID: {proc.pid}\n")
        threading.Thread(target=read_output, args=(proc.stdout, "STDOUT"), daemon=True).start()
        threading.Thread(target=read_output, args=(proc.stderr, "STDERR"), daemon=True).start()
        with open(log_path, "a") as log:
            log.write("Waiting for Streamlit server to start...\n")
        time.sleep(8)
        # Create the window without the icon parameter
        with open(log_path, "a") as log:
            log.write("Creating WebView window...\n")
        window = webview.create_window("Trace Document Tracker", "http://127.0.0.1:8501")
        # Set the icon for the window on Windows
        if os.name == 'nt':
            set_window_icon(base_dir, log_path, window)
        webview.start()
        with open(log_path, "a") as log:
            log.write("WebView closed, terminating Streamlit...\n")
        proc.terminate()
        proc.wait(timeout=5)
    except Exception as e:
        with open(log_path, "a") as log:
            log.write(f"Error: {str(e)}\n")
            import traceback
            log.write(traceback.format_exc())
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(0, f"Error starting application: {str(e)}", "Error", 0x10)

if __name__ == "__main__":
    main()