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
    # Standard location for application data
    app_name = "TraceDocumentTracker"
    
    # Use %LOCALAPPDATA% for Windows
    if os.name == 'nt':
        app_data = os.path.join(os.environ.get('LOCALAPPDATA', ''), app_name)
    # Use ~/.local/share for Linux
    elif os.name == 'posix':
        app_data = os.path.join(os.path.expanduser('~'), '.local', 'share', app_name)
    # Use ~/Library/Application Support for macOS
    elif os.name == 'darwin':
        app_data = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', app_name)
    else:
        # Fallback to a directory next to the executable
        app_data = os.path.join(os.path.dirname(sys.executable), 'AppData')
    
    # Create the directory if it doesn't exist
    os.makedirs(app_data, exist_ok=True)
    return app_data

def ensure_app_files(app_data_dir, base_dir):
    """Ensure all necessary application files are in the app data directory."""
    log_path = os.path.join(app_data_dir, "launcher.log")
    
    with open(log_path, "a") as log:
        log.write(f"Ensuring application files in: {app_data_dir}\n")
    
    # Files to look for and copy if needed
    required_files = ["document_tracker.py", "entries.json"]
    
    # Places to look for required files
    search_locations = [
        base_dir,  # Executable directory
        os.path.dirname(base_dir),  # Parent directory
        os.getcwd()  # Current working directory
    ]
    
    # Add the installation directory if this was installed
    program_files = os.environ.get('PROGRAMFILES', '')
    if program_files:
        install_dir = os.path.join(program_files, "TraceDocumentTracker")
        search_locations.append(install_dir)
    
    # Initialize a dictionary to store found file paths
    found_files = {}
    
    # Search for each required file
    for file in required_files:
        # Skip if file already exists in app data
        if os.path.exists(os.path.join(app_data_dir, file)):
            found_files[file] = os.path.join(app_data_dir, file)
            with open(log_path, "a") as log:
                log.write(f"Found {file} already in app data directory\n")
            continue
        
        # Search in potential locations
        for location in search_locations:
            file_path = os.path.join(location, file)
            if os.path.exists(file_path):
                # Copy file to app data directory
                dest_path = os.path.join(app_data_dir, file)
                shutil.copy2(file_path, dest_path)
                found_files[file] = dest_path
                with open(log_path, "a") as log:
                    log.write(f"Copied {file} from {file_path} to {dest_path}\n")
                break
        
        # If file not found, try creating default versions
        if file not in found_files:
            if file == "entries.json":
                # Create a default empty entries.json
                default_entries = {"documents": []}
                with open(os.path.join(app_data_dir, file), 'w') as f:
                    json.dump(default_entries, f)
                found_files[file] = os.path.join(app_data_dir, file)
                with open(log_path, "a") as log:
                    log.write(f"Created default {file} in app data directory\n")
    
    # Log found file status
    with open(log_path, "a") as log:
        log.write(f"Files found: {found_files}\n")
    
    return found_files

def find_python_executable():
    """Find a valid Python executable."""
    # Try using system Python first
    try:
        result = subprocess.run(["python", "--version"], 
                               stdout=subprocess.PIPE, 
                               stderr=subprocess.PIPE, 
                               text=True)
        if result.returncode == 0:
            return "python"
    except FileNotFoundError:
        pass
    
    # Common Python installation paths
    possible_paths = [
        # Python 3.13
        r"C:\Python313\python.exe",
        r"C:\Program Files\Python\Python313\python.exe",
        r"C:\Program Files\Python313\python.exe",
        r"C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python313\python.exe",
        # Python 3.12
        r"C:\Python312\python.exe",
        r"C:\Program Files\Python\Python312\python.exe",
        r"C:\Program Files\Python312\python.exe",
        r"C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe",
        # Python 3.11
        r"C:\Python311\python.exe",
        r"C:\Program Files\Python\Python311\python.exe",
        r"C:\Program Files\Python311\python.exe",
        r"C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe",
    ]
    
    # Expand %USERNAME%
    username = os.environ.get('USERNAME', '')
    possible_paths = [p.replace('%USERNAME%', username) for p in possible_paths]
    
    # Check each path
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    # If no Python found, return None
    return None

def check_streamlit_installed(python_path, log_path):
    """Check if streamlit is installed and install if needed."""
    try:
        result = subprocess.run(
            [python_path, "-m", "streamlit", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        if result.returncode == 0:
            with open(log_path, "a") as log:
                log.write(f"Streamlit is installed: {result.stdout.strip()}\n")
            return True
        else:
            with open(log_path, "a") as log:
                log.write(f"Streamlit not found, attempting to install...\n")
            
            # Install streamlit
            install_result = subprocess.run(
                [python_path, "-m", "pip", "install", "streamlit"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
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

def main():
    # Get base directory (where the executable is)
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Get application data directory
    app_data_dir = get_app_data_dir()
    
    # Set up logging
    log_path = os.path.join(app_data_dir, "launcher.log")
    
    # Create/overwrite log file
    with open(log_path, "w") as log:
        log.write(f"Launcher started at {time.ctime()}\n")
        log.write(f"Base directory: {base_dir}\n")
        log.write(f"App data directory: {app_data_dir}\n")
    
    # Ensure all application files are available
    found_files = ensure_app_files(app_data_dir, base_dir)
    
    # Get document_tracker.py path
    document_path = found_files.get("document_tracker.py")
    if not document_path:
        with open(log_path, "a") as log:
            log.write("Critical error: document_tracker.py not found\n")
        # Show error message to user
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(0, 
                "Cannot find document_tracker.py. The application cannot start.", 
                "Error", 0x10)
        return
    
    # Find Python executable
    python_path = find_python_executable()
    if not python_path:
        with open(log_path, "a") as log:
            log.write("Critical error: Python not found\n")
        # Show error message to user
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(0, 
                "Python is not installed or cannot be found. Please install Python 3.11 or later.", 
                "Error", 0x10)
        return
    
    with open(log_path, "a") as log:
        log.write(f"Python path: {python_path}\n")
    
    # Check for streamlit and install if needed
    if not check_streamlit_installed(python_path, log_path):
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(0, 
                "Streamlit installation failed. Please install Streamlit manually using pip.", 
                "Error", 0x10)
        return
    
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
        
        # Set working directory to app data directory
        with open(log_path, "a") as log:
            log.write(f"Working directory: {app_data_dir}\n")
        
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=app_data_dir,
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
            import traceback
            log.write(traceback.format_exc())
        
        # Show error message to user
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(0, 
                f"Error starting application: {str(e)}", 
                "Error", 0x10)

if __name__ == "__main__":
    main()