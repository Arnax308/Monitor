import streamlit as st
import json, os, socket, threading, time, difflib, base64, logging
from datetime import datetime

# Constants
ENTRIES_FILE = "entries.json"
WHITEBOARD_FILE = "whiteboard.txt"
BROADCAST_PORT = 12345
DISCOVERY_PORT = 12346
NOTIFICATION_PORT = 12347

# File operations
def load_entries():
    try:
        with open(ENTRIES_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_entries(entries):
    with open(ENTRIES_FILE, "w") as f:
        json.dump(entries, f)

def load_whiteboard():
    try:
        with open(WHITEBOARD_FILE, "r") as f:
            return f.read()
    except FileNotFoundError:
        return ""

def save_whiteboard(content):
    with open(WHITEBOARD_FILE, "w") as f:
        f.write(content)
    log_action("update_whiteboard", st.session_state.network.hostname, {"content_length": len(content)})

def create_completion_file(unique_id, name, history):
    filename = f"{unique_id}_{name}.txt"
    content = f"Client: {name}\nID: {unique_id}\n\nHistory:\n" + "\n".join(
        [f"[{h['timestamp']}] {h['computer']}: {h['message']}" for h in history]
    )
    with open(filename, "w") as f:
        f.write(content)
    return filename, content

def get_download_link(filename, text_content):
    b64 = base64.b64encode(text_content.encode()).decode()
    return f'<a href="data:file/txt;base64,{b64}" download="{filename}">Download {filename}</a>'

# Logging
def setup_logging():
    os.makedirs("logs", exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = f"logs/client_tracker_{today}.log"
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.info("Client Tracker application started")
    return log_file

def log_action(action_type, user, details=None):
    details = details or {}
    detail_str = " - " + " - ".join(f"{k}:{v}" for k, v in details.items()) if details else ""
    logging.info(f"ACTION:{action_type} - USER:{user}{detail_str}")

# P2P Networking for LAN
class P2PNetwork:
    def __init__(self):
        self.peers = set()
        self.hostname = socket.gethostname()
        try:
            self.ip = socket.gethostbyname(self.hostname)
        except:
            self.ip = "127.0.0.1"
        self.entries_lock = threading.Lock()
        self.whiteboard_lock = threading.Lock()
        self.running = True
        # Start discovery and broadcast threads
        for target in (self.discovery_sender, self.discovery_listener, self.broadcast_listener):
            threading.Thread(target=target, daemon=True).start()

    def discovery_sender(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.running:
            try:
                s.sendto(f"DISCOVERY:{self.ip}".encode(), ('<broadcast>', DISCOVERY_PORT))
                time.sleep(15)
            except Exception as e:
                print(f"Discovery sender error: {e}")
                time.sleep(5)

    def discovery_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(('', DISCOVERY_PORT))
            while self.running:
                try:
                    data, _ = listener.recvfrom(1024)
                    msg = data.decode()
                    if msg.startswith("DISCOVERY:"):
                        peer_ip = msg.split(":")[1]
                        if peer_ip != self.ip:
                            self.peers.add(peer_ip)
                except Exception as e:
                    print(f"Discovery listener error: {e}")
        except Exception as e:
            print(f"Failed to bind discovery listener: {e}")

    def broadcast_listener(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(('', BROADCAST_PORT))
            server.listen(10)
            while self.running:
                try:
                    client, _ = server.accept()
                    data = b""
                    while (chunk := client.recv(4096)):
                        data += chunk
                    if data:
                        payload = json.loads(data.decode())
                        if payload.get('type') == 'entries':
                            self.process_received_entries(payload['data'])
                        elif payload.get('type') == 'whiteboard':
                            self.process_received_whiteboard(payload['data'])
                    client.close()
                except Exception as e:
                    print(f"Broadcast listener error: {e}")
        except Exception as e:
            print(f"Failed to bind broadcast listener: {e}")

    def process_received_entries(self, received_entries):
        with self.entries_lock:
            local_entries = load_entries()
            updated = False
            for uid, recv in received_entries.items():
                if uid not in local_entries:
                    local_entries[uid] = recv; updated = True; continue
                local = local_entries[uid]
                if len(local['history']) < len(recv['history']) or (not local.get('completed') and recv.get('completed')):
                    local_entries[uid] = recv; updated = True; continue
                local_ts = {h['timestamp'] for h in local['history']}
                for h in recv['history']:
                    if h['timestamp'] not in local_ts:
                        local['history'].append(h)
                        local['history'].sort(key=lambda x: x['timestamp'])
                        updated = True
                if recv.get('completed') and not local.get('completed'):
                    local['completed'] = True; updated = True
            if updated:
                save_entries(local_entries)
                if 'entries' in st.session_state:
                    st.session_state.entries = local_entries
                return True
        return False

    def process_received_whiteboard(self, received_content):
        with self.whiteboard_lock:
            local = load_whiteboard()
            if not local:
                save_whiteboard(received_content)
                return True
            if local == received_content:
                return False
            diff = list(difflib.Differ().compare(local.splitlines(), received_content.splitlines()))
            merged = [line[2:] for line in diff if line.startswith('  ') or line.startswith('+ ')]
            merged_content = '\n'.join(merged)
            save_whiteboard(merged_content)
            return True

    def broadcast_entries(self, entries_to_broadcast):
        if not self.peers:
            print("No known peers to broadcast to")
            return
        payload = json.dumps({'type': 'entries', 'data': entries_to_broadcast}).encode()
        threading.Thread(target=self._broadcast_task, args=(payload, 'entries'), daemon=True).start()

    def _broadcast_task(self, payload_json, task_type):
        for peer_ip in self.peers:
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client.settimeout(2)
                client.connect((peer_ip, BROADCAST_PORT))
                client.sendall(payload_json)
                client.close()
                print(f"Successfully broadcast {task_type} to {peer_ip}")
            except Exception as e:
                print(f"Failed to broadcast {task_type} to {peer_ip}: {e}")

    def broadcast_whiteboard(self, content):
        if not self.peers:
            print("No known peers to broadcast to")
            return
        payload = json.dumps({'type': 'whiteboard', 'data': content}).encode()
        threading.Thread(target=self._broadcast_task, args=(payload, 'whiteboard'), daemon=True).start()

# Notification System
class NotificationSystem:
    def __init__(self, network):
        self.network = network
        self.hostname = network.hostname
        self.ip = network.ip
        self.running = True
        threading.Thread(target=self.notification_listener, daemon=True).start()
        logging.info(f"Notification system initialized on {self.hostname} ({self.ip})")

    def notification_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(('', NOTIFICATION_PORT))
            logging.info(f"Notification listener started on port {NOTIFICATION_PORT}")
            while self.running:
                try:
                    data, _ = listener.recvfrom(1024)
                    notif = json.loads(data.decode())
                    logging.info(f"Received notification: {notif['type']} - {notif['message']}")
                except Exception as e:
                    logging.error(f"Notification listener error: {e}")
        except Exception as e:
            logging.error(f"Failed to bind notification listener: {e}")

    def send_notification(self, notification_type, message, details=None):
        notification = {
            'type': notification_type,
            'message': message,
            'source': self.hostname,
            'timestamp': time.strftime("%d-%m-%Y %H:%M:%S"),
            'details': details or {}
        }
        payload = json.dumps(notification).encode()
        threading.Thread(target=self._send_notification_task, args=(payload,), daemon=True).start()
        logging.info(f"Notification sent: {notification_type} - {message}")

    def _send_notification_task(self, payload):
        for peer_ip in self.network.peers:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(payload, (peer_ip, NOTIFICATION_PORT))
                sock.close()
            except Exception as e:
                logging.error(f"Failed to send notification to {peer_ip}: {e}")

# Callback functions
def submit_new_entry(client_name, unique_id, initial_message, computer_name):
    if not unique_id or not client_name:
        return False, "Both client name and unique ID are required"
    new_entry = {
        "name": client_name,
        "history": [{
            "timestamp": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            "computer": computer_name,
            "message": initial_message
        }],
        "completed": False
    }
    with st.session_state.network.entries_lock:
        entries = load_entries()
        entries[unique_id] = new_entry
        save_entries(entries)
        st.session_state.entries = entries
    log_action("create_entry", computer_name, {"client_name": client_name, "unique_id": unique_id})
    note = f"New entry created for client {client_name} (ID: {unique_id})"
    st.session_state.notification_system.send_notification("new_entry", note, {"client_name": client_name, "unique_id": unique_id})
    st.session_state.network.broadcast_entries({unique_id: new_entry})
    st.session_state.client_name, st.session_state.unique_id, st.session_state.initial_message = "", "", ""
    st.session_state.form_submitted = True
    return True, "New entry created and broadcasted"

def update_entry(unique_id, message, computer_name):
    if not message.strip():
        return False, "Update message cannot be empty"
    new_update = {
        "timestamp": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        "computer": computer_name,
        "message": message
    }
    with st.session_state.network.entries_lock:
        entries = load_entries()
        if unique_id not in entries:
            return False, f"No entry found with ID: {unique_id}"
        client_name = entries[unique_id]['name']
        entries[unique_id]['history'].append(new_update)
        save_entries(entries)
        st.session_state.entries = entries
    log_action("update_entry", computer_name, {
        "unique_id": unique_id,
        "client_name": client_name,
        "message_preview": message[:50] + ("..." if len(message) > 50 else "")
    })
    note = f"Entry updated for client {client_name} (ID: {unique_id})"
    st.session_state.notification_system.send_notification("update_entry", note, {"client_name": client_name, "unique_id": unique_id})
    st.session_state.network.broadcast_entries({unique_id: st.session_state.entries[unique_id]})
    return True, "Update submitted and broadcasted"

def complete_entry(unique_id):
    with st.session_state.network.entries_lock:
        entries = load_entries()
        if unique_id not in entries:
            return False, f"No entry found with ID: {unique_id}"
        client_name = entries[unique_id]['name']
        computer_name = st.session_state.network.hostname
        entries[unique_id]['completed'] = True
        save_entries(entries)
        st.session_state.entries = entries
    log_action("complete_entry", computer_name, {"unique_id": unique_id, "client_name": client_name})
    note = f"Entry marked as completed for client {client_name} (ID: {unique_id})"
    st.session_state.notification_system.send_notification("complete_entry", note, {"client_name": client_name, "unique_id": unique_id})
    st.session_state.network.broadcast_entries({unique_id: st.session_state.entries[unique_id]})
    return True, "Entry marked as completed"

# Custom styling (UI remains unchanged)
def set_page_style():
    st.markdown("""
    <style>
    /* Base styles */
    body { color: #e0e0e0; background-color: #121212; }
    label, .stTextInput label, .stTextArea label, .stSelectbox label { color: #e0e0e0 !important; font-weight: 500; font-size: 1rem; }
    .main-header { font-size: 2.5rem; font-weight: 700; margin-bottom: 1.5rem; color: #ffffff; }
    .section-header { font-size: 1.8rem; font-weight: 600; margin-bottom: 1rem; color: #ffffff; }
    .tab-header { font-size: 1.5rem; font-weight: 600; margin-bottom: 1rem; color: #ffffff; }
    .card-title { font-size: 1.3rem; font-weight: 600; color: #82b1ff; }
    input, textarea, .stTextInput input, .stTextArea textarea { color: #e0e0e0; background-color: #1e1e1e; border: 1px solid #333333; border-radius: 4px; }
    .stTextArea { min-height: 120px; }
    .stExpander { border: 1px solid #333333; border-radius: 8px; margin-bottom: 1rem; box-shadow: 0 4px 6px rgba(0,0,0,0.3); background-color: #1e1e1e; }
    .stExpander > details { padding: 1.2rem; color: #e0e0e0; }
    .stExpander > details > summary { font-size: 1.2rem; font-weight: 600; padding: 0.8rem; color: #82b1ff; }
    .history-item { margin: 0.5rem 0; padding: 0.8rem; background-color: #252525; border-radius: 4px; color: #e0e0e0; border-left: 3px solid #82b1ff; }
    .stButton > button { padding: 0.5rem 1rem; font-weight: 600; background-color: #2979ff; color: white; border: none; border-radius: 4px; transition: background-color 0.3s ease; }
    .stButton > button:hover { background-color: #1565c0; }
    .info-message { background-color: #1e293b; border: 1px solid #334155; color: #94a3b8; padding: 12px; border-radius: 5px; margin-bottom: 15px; }
    .css-1d391kg, .css-1lcbmhc { background-color: #1a1a1a; }
    .network-status { font-size: 1.1rem; padding: 12px; background-color: #1e293b; border-radius: 5px; color: #e0e0e0; border: 1px solid #334155; }
    .stTabs [data-baseweb="tab-list"] { gap: 2px; background-color: #1a1a1a; border-radius: 4px; }
    .stTabs [data-baseweb="tab"] { padding: 10px 16px; background-color: #1a1a1a; color: #e0e0e0; transition: background-color 0.3s ease; }
    .stTabs [aria-selected="true"] { background-color: #2979ff; color: white; }
    .stTable { border-collapse: collapse; }
    .stTable th { background-color: #252525; color: #e0e0e0; font-weight: 600; padding: 8px; }
    .stTable td { padding: 8px; border: 1px solid #333333; background-color: #1e1e1e; color: #e0e0e0; }
    .stAlert { background-color: #1e1e1e; color: #e0e0e0; border-radius: 4px; }
    .element-container .stAlert.success { background-color: rgba(46, 125, 50, 0.2); border-left: 4px solid #2e7d32; }
    .element-container .stAlert.error { background-color: rgba(211, 47, 47, 0.2); border-left: 4px solid #d32f2f; }
    a { color: #82b1ff; text-decoration: none; font-weight: 500; }
    a:hover { text-decoration: underline; }
    .stSelectbox > div[data-baseweb="select"] { background-color: #1e1e1e; border: 1px solid #333333; }
    .stSelectbox > div[data-baseweb="select"] > div { color: #e0e0e0; }
    .stProgress > div > div { background-color: #2979ff; }
    .stCheckbox label { color: #e0e0e0; }
    .stCheckbox label span { border-color: #555555; }
    .stRadio label { color: #e0e0e0; }
    .main .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    div.stNumberInput > div { background-color: #1e1e1e; }
    div.stNumberInput > div > div > input { color: #e0e0e0; }
    .status-badge-active { background-color: #2979ff; color: white; padding: 5px 10px; border-radius: 15px; font-size: 0.9rem; display: inline-block; }
    .status-badge-completed { background-color: #00c853; color: white; padding: 5px 10px; border-radius: 15px; font-size: 0.9rem; display: inline-block; }
    .download-container { margin-top: 20px; padding: 15px; background-color: #252525; border-radius: 5px; text-align: center; border: 1px solid #333333; }
    .whiteboard-note { margin-bottom: 15px; padding: 12px; background-color: #1e293b; border-radius: 5px; color: #94a3b8; border: 1px solid #334155; }
    </style>
    """, unsafe_allow_html=True)

# Main application
def main():
    log_file = setup_logging()
    logging.info(f"Application started, logging to {log_file}")
    set_page_style()
    st.markdown('<h1 class="main-header">Client Tracker</h1>', unsafe_allow_html=True)

    if 'network' not in st.session_state:
        st.session_state.network = P2PNetwork()
    if 'notification_system' not in st.session_state:
        st.session_state.notification_system = NotificationSystem(st.session_state.network)

    defaults = {
        'entries': load_entries(),
        'whiteboard': load_whiteboard(),
        'client_name': "",
        'unique_id': "",
        'initial_message': "",
        'action_status': None,
        'current_search_id': None,
        'reset_form_flag': False,
        'last_reload': time.time()
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    # Periodic reload
    if time.time() - st.session_state.last_reload > 10:
        st.session_state.entries = load_entries()
        st.session_state.whiteboard = load_whiteboard()
        st.session_state.last_reload = time.time()

    if st.session_state.reset_form_flag:
        for field in ['client_name', 'unique_id', 'initial_message']:
            st.session_state[field] = ""
        st.session_state.reset_form_flag = False

    if st.session_state.action_status:
        success, message = st.session_state.action_status
        (st.success(message) if success else st.error(message))
        st.session_state.action_status = None

    # Sidebar
    st.sidebar.markdown(f'''
    <div class="network-status">
        Connected to <b>{len(st.session_state.network.peers)}</b> peers
    </div>
    ''', unsafe_allow_html=True)
    if st.sidebar.button("Force Refresh", use_container_width=True):
        st.session_state.entries = load_entries()
        st.session_state.whiteboard = load_whiteboard()
        st.rerun()

    # Main tabs
    tab1, tab2, tab3 = st.tabs(["Home", "Search", "Whiteboard"])
    with tab1:
        st.markdown('<h2 class="tab-header">Client Tracker</h2>', unsafe_allow_html=True)
        with st.expander("Create New Entry", expanded=True):
            with st.form("new_entry_form"):
                st.markdown('<p class="card-title">New Client Entry</p>', unsafe_allow_html=True)
                client_name = st.text_input("Client Name:", key="client_name_input", value=st.session_state.client_name)
                unique_id = st.text_input("Unique ID:", key="unique_id_input", value=st.session_state.unique_id)
                initial_message = st.text_area("Initial Notes:", key="initial_message_input", value=st.session_state.initial_message, height=150)
                computer_name = st.text_input("Computer name:", value=st.session_state.network.hostname)
                if st.form_submit_button("Create Entry", use_container_width=True):
                    st.session_state.action_status = submit_new_entry(client_name, unique_id, initial_message, computer_name)
                    st.rerun()
            if st.button("Clear Form", key="clear_form_btn", use_container_width=True):
                st.session_state.reset_form_flag = True
                st.rerun()

        st.markdown('<h3 class="section-header">Active Entries</h3>', unsafe_allow_html=True)
        active_entries = {uid: entry for uid, entry in st.session_state.entries.items() if not entry.get('completed', False)}
        if not active_entries:
            st.markdown("""
            <div class="info-message">
                No active entries. Create one using the form above.
            </div>
            """, unsafe_allow_html=True)
        for uid, entry in active_entries.items():
            with st.expander(f"{uid} - {entry['name']}"):
                st.markdown(f'<p class="card-title">Client: {entry["name"]}</p>', unsafe_allow_html=True)
                st.markdown("<strong>History:</strong>", unsafe_allow_html=True)
                for hist in entry['history']:
                    st.markdown(f"""
                    <div class="history-item">
                        <strong>[{hist['timestamp']}]</strong> {hist['computer']}: {hist['message']}
                    </div>
                    """, unsafe_allow_html=True)
                with st.form(key=f"update_form_{uid}"):
                    st.markdown('<p class="card-title">Add Update</p>', unsafe_allow_html=True)
                    update_message = st.text_area("Add update:", height=120)
                    computer_name = st.text_input("Computer name:", value=st.session_state.network.hostname)
                    cols = st.columns(2)
                    with cols[0]:
                        if st.form_submit_button("Submit Update", use_container_width=True):
                            st.session_state.action_status = update_entry(uid, update_message, computer_name)
                            st.rerun()
                    with cols[1]:
                        if st.form_submit_button("Mark as Completed", use_container_width=True):
                            st.session_state.action_status = complete_entry(uid)
                            st.rerun()

    with tab2:
        st.markdown('<h2 class="tab-header">Search Entries</h2>', unsafe_allow_html=True)
        col1, col2 = st.columns([3, 1])
        with col1:
            search_id = st.text_input("Search by ID:", key="search_id_input", placeholder="Enter client ID")
        with col2:
            if st.button("Search", key="search_button", use_container_width=True) and search_id:
                st.session_state.current_search_id = search_id
        if st.session_state.current_search_id in st.session_state.entries:
            sid = st.session_state.current_search_id
            entry = st.session_state.entries[sid]
            with st.expander(f"{sid} - {entry['name']}", expanded=True):
                st.markdown(f"""
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <p class="card-title">Client: {entry['name']}</p>
                        <span class="status-badge-{'completed' if entry.get('completed', False) else 'active'}">
                            {"Completed" if entry.get('completed', False) else "Active"}
                        </span>
                    </div>
                    """, unsafe_allow_html=True)
                st.markdown("<strong>History:</strong>", unsafe_allow_html=True)
                for hist in entry['history']:
                    st.markdown(f"""
                    <div class="history-item">
                        <strong>[{hist['timestamp']}]</strong> {hist['computer']}: {hist['message']}
                    </div>
                    """, unsafe_allow_html=True)
                if entry.get('completed', False):
                    filename, content = create_completion_file(sid, entry['name'], entry['history'])
                    st.markdown(f"""
                    <div class="download-container">
                        {get_download_link(filename, content)}
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    with st.form(key=f"search_update_form_{sid}"):
                        update_message = st.text_area("Add update:", height=120)
                        computer_name = st.text_input("Computer name:", value=st.session_state.network.hostname)
                        cols = st.columns(2)
                        with cols[0]:
                            if st.form_submit_button("Submit Update", use_container_width=True):
                                st.session_state.action_status = update_entry(sid, update_message, computer_name)
                                st.rerun()
                        with cols[1]:
                            if st.form_submit_button("Mark as Completed", use_container_width=True):
                                st.session_state.action_status = complete_entry(sid)
                                st.rerun()

    with tab3:
        st.markdown('<h2 class="tab-header">Whiteboard</h2>', unsafe_allow_html=True)
        st.markdown("""
        <div class="whiteboard-note">
            Use this area to connect IDs with names or any other notes. Changes are automatically shared with connected peers.
        </div>
        """, unsafe_allow_html=True)
        whiteboard_content = st.text_area("Whiteboard", value=st.session_state.whiteboard, height=500, key="whiteboard_content")
        if whiteboard_content != st.session_state.whiteboard:
            with st.session_state.network.whiteboard_lock:
                save_whiteboard(whiteboard_content)
                st.session_state.whiteboard = whiteboard_content
                st.session_state.network.broadcast_whiteboard(whiteboard_content)
                st.success("Whiteboard updated and changes broadcasted")

if __name__ == "__main__":
    main()
