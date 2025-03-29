import streamlit as st
import json, os, socket, threading, time, difflib, base64, logging
from datetime import datetime, timedelta
from collections import defaultdict

# Constants
ENTRIES_FILE = "entries.json"
WHITEBOARD_FILE = "whiteboard.txt"
BROADCAST_PORT = 12345
DISCOVERY_PORT = 12346
NOTIFICATION_PORT = 12347

# Priority levels
PRIORITY_LEVELS = ["Low", "Medium", "High", "Highest"]

def load_entries():
    try:
        with open(ENTRIES_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_entries(entries):
    with open(ENTRIES_FILE, "w") as f:
        json.dump(entries, f, indent=2)

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
    try:
        with open(filename, "w") as f:
            f.write(content)
        return filename, content
    except IOError as e:
        logging.error(f"File creation error: {e}")
        return None, None

def is_deadline_approaching(deadline_str):
    if not deadline_str:
        return False
    try:
        deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d")
        return timedelta(0) < (deadline_date - datetime.now()) <= timedelta(hours=48)
    except ValueError:
        return False

def setup_logging():
    os.makedirs("logs", exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = f"logs/monitor_{today}.log"
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.info("Monitor started")
    return log_file

def log_action(action_type, user, details=None):
    details = details or {}
    detail_str = " - ".join(f"{k}:{v}" for k, v in details.items())
    logging.info(f"ACTION:{action_type} - USER:{user} - {detail_str}")

class P2PNetwork:
    def __init__(self):
        self.peers = set()
        self.hostname = socket.gethostname()
        self.ip = self._get_local_ip()
        self.entries_lock = threading.Lock()
        self.whiteboard_lock = threading.Lock()
        self.running = True
        
        # Start network threads
        for target in (self.discovery_sender, self.discovery_listener, self.broadcast_listener):
            threading.Thread(target=target, daemon=True).start()

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def discovery_sender(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.running:
            try:
                s.sendto(f"DISCOVERY:{self.ip}".encode(), ('<broadcast>', DISCOVERY_PORT))
                time.sleep(15)
            except Exception as e:
                logging.error(f"Discovery sender error: {e}")
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
                        if peer_ip != self.ip and peer_ip not in self.peers:
                            self.peers.add(peer_ip)
                            logging.info(f"Discovered new peer: {peer_ip}")
                except Exception as e:
                    logging.error(f"Discovery listener error: {e}")
        except Exception as e:
            logging.error(f"Failed to bind discovery listener: {e}")

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
                    logging.error(f"Broadcast listener error: {e}")
        except Exception as e:
            logging.error(f"Failed to bind broadcast listener: {e}")

    def process_received_entries(self, received_entries):
        with self.entries_lock:
            local_entries = load_entries()
            updated = False
            for uid, recv in received_entries.items():
                if uid not in local_entries:
                    local_entries[uid] = recv
                    updated = True
                    continue
                
                local = local_entries[uid]
                if (len(local['history']) < len(recv['history']) or
                    (not local.get('completed') and recv.get('completed'))):
                    local_entries[uid] = recv
                    updated = True
                    continue
                
                local_ts = {h['timestamp'] for h in local['history']}
                for h in recv['history']:
                    if h['timestamp'] not in local_ts:
                        local['history'].append(h)
                        updated = True
                local['history'].sort(key=lambda x: x['timestamp'])
                
                if recv.get('completed') and not local.get('completed'):
                    local['completed'] = True
                    updated = True
            
            if updated:
                save_entries(local_entries)
                st.session_state.entries = local_entries
                logging.info("Successfully merged received entries")
            return updated

    def broadcast_entries(self, entries_to_broadcast):
        if not self.peers:
            logging.warning("No peers to broadcast entries to")
            return
        
        payload = json.dumps({'type': 'entries', 'data': entries_to_broadcast}).encode()
        threading.Thread(target=self._broadcast_task, args=(payload, 'entries'), daemon=True).start()

    def broadcast_whiteboard(self, content):
        if not self.peers:
            logging.warning("No peers to broadcast whiteboard to")
            return

        payload = json.dumps({'type': 'whiteboard', 'data': content}).encode()
        threading.Thread(target=self._broadcast_task, args=(payload, 'whiteboard'), daemon=True).start()

    def process_received_whiteboard(self, received_content):
        with self.whiteboard_lock:
            local_content = load_whiteboard()
            if received_content != local_content:
                save_whiteboard(received_content)
                st.session_state.whiteboard = received_content
                logging.info("Whiteboard updated from broadcasted content")

    def _broadcast_task(self, payload_json, task_type):
        for peer_ip in self.peers:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    client.connect((peer_ip, BROADCAST_PORT))
                    client.sendall(payload_json)
                logging.debug(f"Broadcast {task_type} to {peer_ip} successful")
            except Exception as e:
                logging.error(f"Failed to broadcast {task_type} to {peer_ip}: {e}")

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
                    self._handle_notification(notif)
                except json.JSONDecodeError:
                    logging.warning("Received invalid notification format")
                except Exception as e:
                    logging.error(f"Notification listener error: {e}")
        except Exception as e:
            logging.error(f"Failed to bind notification listener: {e}")

    def _handle_notification(self, notification):
        logging.info(f"Received notification: {notification['type']} - {notification['message']}")
        if notification['type'] in ['new_entry', 'update_entry']:
            st.toast(f"{notification['source']}: {notification['message']}", icon="ℹ️")
        elif notification['type'] == 'priority_update':
            st.toast(f"System Alert: {notification['message']}", icon="⚠️")

    def send_notification(self, notification_type, message, details=None):
        notification = {
            'type': notification_type,
            'message': message,
            'source': self.hostname,
            'timestamp': datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            'details': details or {}
        }
        payload = json.dumps(notification).encode()
        threading.Thread(target=self._send_notification_task, args=(payload,), daemon=True).start()
        logging.info(f"Notification queued: {notification_type} - {message}")

    def _send_notification_task(self, payload):
        for peer_ip in self.network.peers:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(1)
                    sock.sendto(payload, (peer_ip, NOTIFICATION_PORT))
                logging.debug(f"Notification sent to {peer_ip}")
            except socket.timeout:
                logging.warning(f"Timeout sending notification to {peer_ip}")
            except Exception as e:
                logging.error(f"Failed to send notification to {peer_ip}: {e}")

    def stop(self):
        self.running = False
        logging.info("Notification system stopped")

def update_entry_priority(entries):
    updated = False
    for uid, entry in entries.items():
        if entry.get('completed', False):
            continue
        deadline_str = entry.get('deadline')
        if deadline_str and is_deadline_approaching(deadline_str):
            if entry.get('priority') != "Highest":
                entry['priority'] = "Highest"
                updated = True
                log_action("priority_update", "system", {
                    "unique_id": uid, 
                    "client_name": entry['name'],
                    "reason": "Deadline approaching"
                })
    return updated

def search_entries_by_name(entries, name_query):
    if not name_query:
        return []
    
    name_query = name_query.lower()
    return [
        (uid, entry) for uid, entry in entries.items()
        if name_query in entry['name'].lower()
    ]

def submit_new_entry(client_name, unique_id, initial_message, computer_name, employee_name, priority, deadline, total_payable, total_paid):
    if not unique_id or not client_name:
        return False, "Both client name and unique ID are required"
    
    try:
        total_payable = float(total_payable) if total_payable else 0.0
        total_paid = float(total_paid) if total_paid else 0.0
        remaining = max(0.0, total_payable - total_paid)
    except ValueError:
        return False, "Invalid payment values"

    new_entry = {
        "name": client_name,
        "history": [{
            "timestamp": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            "computer": computer_name,
            "employee": employee_name,
            "message": initial_message
        }],
        "priority": priority,
        "deadline": deadline,
        "total_payable": str(total_payable),
        "total_paid": str(total_paid),
        "remaining": str(remaining),
    }

    with st.session_state.network.entries_lock:
        entries = load_entries()
        if unique_id in entries:
            return False, "Entry ID already exists"
        entries[unique_id] = new_entry
        save_entries(entries)
        st.session_state.entries = entries
        
    log_action("create_entry", employee_name, {
        "client_name": client_name, 
        "unique_id": unique_id, 
        "priority": priority,
        "deadline": deadline or "None"
    })
    
    note = f"New entry created for client {client_name} (ID: {unique_id})"
    st.session_state.notification_system.send_notification("new_entry", note, {
        "client_name": client_name, 
        "unique_id": unique_id,
        "priority": priority
    })
    
    st.session_state.network.broadcast_entries({unique_id: new_entry})
    st.session_state.form_submitted = True
    return True, "New entry created and broadcasted"

def update_entry(unique_id, message, computer_name, employee_name, payment_made=None):
    if not message.strip() and not payment_made:
        return False, "Update message or payment information cannot be empty"
        
    with st.session_state.network.entries_lock:
        entries = load_entries()
        if unique_id not in entries:
            return False, f"No entry found with ID: {unique_id}"
            
        entry = entries[unique_id]
        client_name = entry['name']
        
        if payment_made:
            try:
                payment = float(payment_made)
                current_paid = float(entry.get('total_paid', "0"))
                new_paid = current_paid + payment
                entry['total_paid'] = str(new_paid)
                
                total_payable = float(entry.get('total_payable', "0"))
                remaining = total_payable - new_paid
                entry['remaining'] = str(max(0, remaining))
                
                payment_msg = f"Payment of ₹{payment} received. Total paid: ₹{new_paid}. Remaining: ₹{entry['remaining']}"
                message = (message + "\n\n" + payment_msg).strip() if message.strip() else payment_msg
            except ValueError:
                return False, "Payment must be a valid number"
                
        new_update = {
            "timestamp": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            "computer": computer_name,
            "employee": employee_name,
            "message": message
        }
        
        entry['history'].append(new_update)
        save_entries(entries)
        st.session_state.entries = entries
        
    log_action("update_entry", employee_name, {
        "unique_id": unique_id,
        "client_name": client_name,
        "payment_made": payment_made if payment_made else "None",
        "message_preview": message[:50]
    })
    
    st.session_state.network.broadcast_entries({unique_id: st.session_state.entries[unique_id]})
    return True, "Update submitted"

def mark_entry_completed(unique_id, computer_name, employee_name):
    with st.session_state.network.entries_lock:
        entries = load_entries()
        if unique_id not in entries:
            return False, f"No entry found with ID: {unique_id}"
            
        entry = entries[unique_id]
        client_name = entry['name']
        
        completion_message = "This entry has been marked as COMPLETED."
        new_update = {
            "timestamp": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            "computer": computer_name,
            "employee": employee_name,
            "message": completion_message
        }
        
        entry['history'].append(new_update)
        entry['completed'] = True
        save_entries(entries)
        st.session_state.entries = entries
        
    log_action("complete_entry", employee_name, {
        "unique_id": unique_id,
        "client_name": client_name
    })
    
    note = f"Entry for client {client_name} (ID: {unique_id}) marked as completed"
    st.session_state.notification_system.send_notification("update_entry", note, {
        "client_name": client_name, 
        "unique_id": unique_id,
        "completed": True
    })
    
    st.session_state.network.broadcast_entries({unique_id: st.session_state.entries[unique_id]})
    return True, "Entry marked as completed

def set_page_style():
    font_size = st.session_state.get('font_size', 1.1)
    st.markdown(f"""
    <style>
    body {{
        color: #e0e0e0;
        background-color: #121212;
        font-size: {font_size}rem;
    }}
    label, .stTextInput label, .stTextArea label, .stSelectbox label {{
        color: #e0e0e0 !important;
        font-weight: 500;
        font-size: 1.15rem;
    }}
    .main-header {{
        font-size: 2.8rem;
        font-weight: 700;
        margin-bottom: 1.8rem;
        color: #ffffff;
    }}
    .section-header {{
        font-size: 2.2rem;
        font-weight: 600;
        margin-bottom: 1.2rem;
        color: #ffffff;
    }}
    .tab-header {{
        font-size: 1.8rem;
        font-weight: 600;
        margin-bottom: 1.2rem;
        color: #ffffff;
    }}
    .card-title {{
        font-size: 2rem;
        font-weight: 600;
        color: #82b1ff;
    }}
    input, textarea, .stTextInput input, .stTextArea textarea {{
        color: #e0e0e0;
        background-color: #1e1e1e;
        border: 1px solid #333333;
        border-radius: 4px;
        font-size: 1.15rem;
    }}
    .stTextArea {{
        min-height: 120px;
    }}
    .stExpander {{
        border: 1px solid #4682B4;
        border-radius: 8px;
        margin-bottom: 1.2rem;
        background-color: #1e1e1e;
        transition: all 0.3s ease;
    }}
    .stExpander > details {{
        margin: 0;
        padding: 0;
    }}
    .stExpander > details > summary {{
        list-style: none;
        outline: none;
        cursor: pointer;
        padding: 14px 18px;
        margin: 0;
        font-size: 1.15rem;
        font-weight: 600;
        color: #ffffff;
        border-radius: 8px;
        transition: background-color 0.3s ease;
    }}
    .stExpander > details > summary:hover {{
        background-color: #2b2b2b;
    }}
    .stExpander > details[open] {{
        background-color: #2b2b2b;
        border-radius: 8px;
        margin-top: -2px;
        border: 1px solid #333333;
    }}
    .stExpander > details[open] > summary {{
        border-bottom-left-radius: 0;
        border-bottom-right-radius: 0;
        background-color: #2b2b2b;
    }}
    .history-item {{
        margin: 0.7rem 0;
        padding: 1rem;
        background-color: #252525;
        border-radius: 4px;
        color: #e0e0e0;
        border-left: 3px solid #82b1ff;
        font-size: 1.15rem;
    }}
    .stButton > button {{
        padding: 0.6rem 1.2rem;
        font-weight: 600;
        background-color: #2979ff;
        color: white;
        border: none;
        border-radius: 4px;
        transition: background-color 0.3s ease;
        font-size: 1.15rem;
    }}
    .stButton > button:hover {{
        background-color: #1565c0;
    }}
    .info-message {{
        background-color: #1e293b;
        border: 1px solid #334155;
        color: #94a3b8;
        padding: 14px;
        border-radius: 5px;
        margin-bottom: 18px;
        font-size: 1.15rem;
    }}
    .css-1d391kg, .css-1lcbmhc {{
        background-color: #1a1a1a;
    }}
    .network-status {{
        font-size: 1.25rem;
        padding: 14px;
        background-color: #1e293b;
        border-radius: 5px;
        color: #e0e0e0;
        border: 1px solid #334155;
    }}
    .stTabs [data-baseweb="tab-list"] {{
        gap: 3px;
        background-color: #1a1a1a;
        border-radius: 4px;
    }}
    .stTabs [data-baseweb="tab"] {{
        padding: 12px 18px;
        background-color: #1a1a1a;
        color: #e0e0e0;
        transition: background-color 0.3s ease;
        font-size: 1.15rem;
    }}
    .stTabs [aria-selected="true"] {{
        background-color: #2979ff;
        color: white;
    }}
    .stTable {{
        border-collapse: collapse;
    }}
    .stTable th {{
        background-color: #252525;
        color: #e0e0e0;
        font-weight: 600;
        padding: 10px;
        font-size: 1.15rem;
    }}
    .stTable td {{
        padding: 10px;
        border: 1px solid #333333;
        background-color: #1e1e1e;
        color: #e0e0e0;
        font-size: 1.15rem;
    }}
    .stAlert {{
        background-color: #1e1e1e;
        color: #e0e0e0;
        border-radius: 4px;
        font-size: 1.15rem;
    }}
    .element-container .stAlert.success {{
        background-color: rgba(46, 125, 50, 0.2);
        border-left: 4px solid #2e7d32;
    }}
    .element-container .stAlert.error {{
        background-color: rgba(211, 47, 47, 0.2);
        border-left: 4px solid #d32f2f;
    }}
    a {{
        color: #82b1ff;
        text-decoration: none;
        font-weight: 500;
        font-size: 1.15rem;
    }}
    a:hover {{
        text-decoration: underline;
    }}
    .stSelectbox > div[data-baseweb="select"] {{
        background-color: #1e1e1e;
        border: 1px solid #333333;
        font-size: 1.15rem;
    }}
    .stSelectbox > div[data-baseweb="select"] > div {{
        color: #e0e0e0;
        font-size: 1.15rem;
    }}
    .stProgress > div > div {{
        background-color: #2979ff;
    }}
    .stCheckbox label {{
        color: #e0e0e0;
        font-size: 1.15rem;
    }}
    .stCheckbox label span {{
        border-color: #555555;
    }}
    .stRadio label {{
        color: #e0e0e0;
        font-size: 1.15rem;
    }}
    .main .block-container {{
        padding-top: 2.2rem;
        padding-bottom: 2.2rem;
    }}
    div.stNumberInput > div {{
        background-color: #1e1e1e;
    }}
    div.stNumberInput > div > div > input {{
        color: #e0e0e0;
        font-size: 1.15rem;
    }}
    .status-badge-active {{
        background-color: #2979ff;
        color: white;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.95rem;
        display: inline-block;
    }}
    .status-badge-completed {{
        background-color: #00c853;
        color: white;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.95rem;
        display: inline-block;
    }}
    .download-container {{
        margin-top: 22px;
        padding: 18px;
        background-color: #252525;
        border-radius: 5px;
        text-align: center;
        border: 1px solid #333333;
    }}
    .whiteboard-note {{
        margin-bottom: 18px;
        padding: 14px;
        background-color: #1e293b;
        border-radius: 5px;
        color: #94a3b8;
        border: 1px solid #334155;
        font-size: 1.15rem;
    }}
    .priority-low {{
        background-color: #2e7d32;
        color: white;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.95rem;
    }}
    .priority-medium {{
        background-color: #ff9800;
        color: white;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.95rem;
    }}
    .priority-high {{
        background-color: #f44336;
        color: white;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.95rem;
    }}
    .priority-highest {{
        background-color: #9c27b0;
        color: white;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.95rem;
        animation: pulse 1.5s infinite;
    }}
    .deadline-info {{
        display: inline-block;
        margin-left: 12px;
        font-style: italic;
        color: #e0e0e0;
        font-size: 1.05rem;
    }}
    .billing-info {{
        margin-top: 10px;
        padding: 10px;
        background-color: #303030;
        border-radius: 4px;
        font-size: 1.15rem;
    }}
    .remaining-amount {{
        color: #f44336;
        font-weight: 600;
    }}
    .fully-paid {{
        color: #4caf50;
        font-weight: 600;
    }}
    .payment-section {{
        margin-top: 14px;
        padding-top: 14px;
        border-top: 1px solid #444;
        font-size: 1.15rem;
        font-weight: 600;
    }}
    .search-results-count {{
        font-size: 1.4rem;
        font-weight: 600;
        margin: 15px 0;
        color: #e0e0e0;
    }}
    .search-no-results {{
        font-size: 1.2rem;
        color: #ff9800;
        text-align: center;
        padding: 20px;
        background-color: rgba(255, 152, 0, 0.1);
        border-radius: 4px;
        margin: 15px 0;
    }}
    @keyframes pulse {{
        0% {{ box-shadow: 0 0 0 0 rgba(156, 39, 176, 0.7); }}
        70% {{ box-shadow: 0 0 0 6px rgba(156, 39, 176, 0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(156, 39, 176, 0); }}
    }}

    /* Card-like style inside the expander */
    .entry-card {{
        border: 2px solid #ffffff;
        background-color: #121212;
        border-radius: 6px;
        padding: 20px;
        margin-bottom: 25px;
        box-shadow: 0 0 8px rgba(255,255,255,0.2);
    }}
    .entry-card-header {{
        display: flex;
        gap: 10px;
        margin-bottom: 8px;
    }}
    .entry-card-deadline {{
        font-style: italic;
        margin-bottom: 12px;
    }}
    .entry-card-section {{
        margin-top: 12px;
        margin-bottom: 6px;
        font-weight: 600;
    }}
    </style>
    """, unsafe_allow_html=True)

def main():
    if 'rerun' not in st.session_state:
        st.session_state.rerun = False

    if st.session_state.rerun:
        st.session_state.rerun = False
        st.rerun()

    # Initialize font size if not set
    if "font_size" not in st.session_state:
        st.session_state.font_size = 1.1

    # Font size controls in sidebar
    st.sidebar.markdown("<h2 class='section-header'>Settings</h2>", unsafe_allow_html=True)
    col_fs1, col_fs2, col_fs3 = st.sidebar.columns([1,1,2])
    if col_fs1.button("A-"):
        st.session_state.font_size = max(0.5, st.session_state.font_size - 0.1)
        st.rerun()
    if col_fs2.button("A+"):
        st.session_state.font_size += 0.1
        st.rerun()
    col_fs3.markdown(f"Current: {st.session_state.font_size:.1f} rem")

    log_file = setup_logging()
    logging.info(f"Application started, logging to {log_file}")
    set_page_style()
    st.markdown('<h1 class="main-header">Monitor</h1>', unsafe_allow_html=True)

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
        'current_search_query': None,
        'search_results': None,
        'reset_form_flag': False,
        'last_reload': time.time(),
        'form_submitted': False
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if time.time() - st.session_state.last_reload > 10:
        st.session_state.entries = load_entries()
        st.session_state.whiteboard = load_whiteboard()
        
        if update_entry_priority(st.session_state.entries):
            save_entries(st.session_state.entries)
            st.session_state.network.broadcast_entries(st.session_state.entries)
            
        st.session_state.last_reload = time.time()

    if st.session_state.reset_form_flag:
        st.session_state.client_name = ""
        st.session_state.unique_id = ""
        st.session_state.initial_message = ""
        st.session_state.reset_form_flag = False

    if st.session_state.get('form_submitted', False):
        st.session_state.form_submitted = False
        st.rerun()

    if st.session_state.action_status:
        success, message = st.session_state.action_status
        if success:
            st.success(message)
        else:
            st.error(message)
        st.session_state.action_status = None

    st.sidebar.markdown('<h2 class="section-header">Network Status</h2>', unsafe_allow_html=True)
    st.sidebar.markdown(
        f'<div class="network-status">Host: {st.session_state.network.hostname}<br>'
        f'IP: {st.session_state.network.ip}<br>'
        f'Connected Peers: {len(st.session_state.network.peers)}</div>',
        unsafe_allow_html=True
    )
    
    if st.session_state.network.peers:
        st.sidebar.markdown("<b>Connected Peers:</b>")
        for peer in st.session_state.network.peers:
            st.sidebar.markdown(f"- {peer}")

    if st.sidebar.button("🔄 Manual Refresh"):
        st.session_state.network.peers = set()
        st.session_state.entries = load_entries()
        st.session_state.whiteboard = load_whiteboard()
        st.rerun()
    
    tab_main, tab_search, tab_whiteboard = st.tabs(["Client Entries", "Search Clients", "Shared Whiteboard"])
    
    # === TAB 1: CLIENT ENTRIES ===
    with tab_main:
        st.markdown('<h2 class="section-header">Client Entries</h2>', unsafe_allow_html=True)

        with st.expander("Create New Entry", expanded=True):
            st.markdown('<h3 class="tab-header">New Client Entry</h3>', unsafe_allow_html=True)
            
            col1, col2 = st.columns(2)
            with col1:
                client_name = st.text_input("Client Name", key="client_name")
                unique_id = st.text_input("Unique ID", key="unique_id")
                employee_name = st.text_input("Employee Name", value=st.session_state.network.hostname)
            with col2:
                priority = st.selectbox("Priority", PRIORITY_LEVELS, index=1)
                deadline = st.date_input("Deadline (optional)", value=None)
            
            col1, col2 = st.columns(2)
            with col1:
                total_payable = st.text_input("Total Payable Amount (₹)", value="")
            with col2:
                total_paid = st.text_input("Initial Payment (₹)", value="0")
                
            initial_message = st.text_area("Initial Notes", key="initial_message", height=120)
            
            if st.button("Submit New Entry"):
                deadline_str = deadline.strftime("%Y-%m-%d") if deadline else ""
                success, message = submit_new_entry(
                    client_name=client_name,
                    unique_id=unique_id,
                    initial_message=initial_message,
                    computer_name=st.session_state.network.hostname,
                    employee_name=employee_name,  
                    priority=priority,
                    deadline=deadline_str,
                    total_payable=total_payable,
                    total_paid=total_paid
                )
                st.session_state.action_status = (success, message)
                st.session_state.form_submitted = True

        # Show only ACTIVE (not completed) entries
        active_entries = {
            uid: entry
            for uid, entry in st.session_state.entries.items()
            if not entry.get('completed', False)
        }

        def priority_sort_key(item):
            uid, entry = item
            return (-PRIORITY_LEVELS.index(entry.get('priority', 'Medium')), entry['name'])

        sorted_active_entries = sorted(active_entries.items(), key=priority_sort_key)

        if sorted_active_entries:
            st.markdown(f'<h3 class="tab-header">Active Entries ({len(sorted_active_entries)})</h3>', unsafe_allow_html=True)
            
            for uid, entry in sorted_active_entries:
                # This expander is labeled with the client name and ID
                with st.expander(f"{entry['name']} (ID: {uid})", expanded=False):
                    
                    priority_class = entry['priority'].lower()

                    # Check days-left text if there's a deadline
                    deadline_text = ""
                    if entry.get('deadline'):
                        from datetime import datetime
                        deadline_date = datetime.strptime(entry['deadline'], "%Y-%m-%d")
                        days_left = (deadline_date - datetime.now()).days
                        if days_left < 0:
                            deadline_text = f"(Overdue by {abs(days_left)} days)"
                        elif days_left == 0:
                            deadline_text = "(Due today)"
                        else:
                            deadline_text = f"({days_left} days left)"

                    # Convert strings to floats for payment logic
                    total_payable = float(entry.get('total_payable', '0'))
                    total_paid = float(entry.get('total_paid', '0'))
                    remaining = float(entry.get('remaining', '0'))

                    # Start our "card" container inside the expander
                    st.markdown(
                        f"""
                        <div class="entry-card">
                            <div class="entry-card-header">
                                <span class="priority-{priority_class}">{entry['priority']}</span>
                                <span class='status-badge-active'>Active</span>
                            </div>
                            <h2 class="card-title">{entry['name']} (ID: {uid})</h2>
                        """,
                        unsafe_allow_html=True
                    )

                    # Show deadline if available
                    if entry.get('deadline'):
                        st.markdown(
                            f"<div class='entry-card-deadline'>Deadline: {entry['deadline']} {deadline_text}</div>",
                            unsafe_allow_html=True
                        )

                    # Payment details
                    st.markdown(
                        """
                        <div class="entry-card-section">
                            <strong>Payment Details:</strong>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                    st.write(f"**Total Payable:** ₹{total_payable}")
                    st.write(f"**Total Paid:** ₹{total_paid}")

                    if remaining > 0:
                        st.markdown(f"**Remaining:** <span class='remaining-amount'>₹{remaining}</span>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"**Remaining:** <span class='fully-paid'>Fully Paid</span>", unsafe_allow_html=True)

                    st.markdown("<hr/>", unsafe_allow_html=True)

                    # Update form
                    new_message = st.text_area(
                        f"Add Update for {entry['name']}",
                        key=f"update_{uid}",
                        height=100,
                        placeholder="Type your update here..."
                    )
                    # New field: Employee Name for the update
                    employee_update = st.text_input(
                        "Employee Name",
                        value=st.session_state.network.hostname,
                        key=f"update_employee_{uid}"
                    )

                    # Show payment input only if there's a remaining balance
                    payment_made = ""
                    if remaining > 0:
                        st.markdown("<div class='payment-section'>Payment Update:</div>", unsafe_allow_html=True)
                        payment_made = st.text_input(
                            "Payment Amount (₹)",
                            key=f"payment_{uid}",
                            placeholder="e.g. 500"
                        )

                    col_btn1, col_btn2 = st.columns([1, 1])
                    with col_btn1:
                        if st.button("Submit Update", key=f"btn_update_{uid}"):
                            success, message = update_entry(
                                unique_id=uid,
                                message=new_message,
                                computer_name=st.session_state.network.hostname,
                                employee_name=employee_update,
                                payment_made=payment_made if remaining > 0 else None
                            )
                            st.session_state.action_status = (success, message)
                            st.session_state.form_submitted = True
                    with col_btn2:
                        if st.button("Mark Completed", key=f"btn_complete_{uid}"):
                            employee_name = st.session_state.network.hostname
                            success, message = mark_entry_completed(
                                unique_id=uid,
                                computer_name=st.session_state.network.hostname,
                                employee_name=employee_name
                            )
                            st.session_state.action_status = (success, message)
                            st.session_state.form_submitted = True

                    st.markdown("<hr/>", unsafe_allow_html=True)

                    # History
                    st.markdown("<h3>History</h3>", unsafe_allow_html=True)
                    for item in reversed(entry['history']):
                        msg_html = item['message'].replace("\n", "<br>")
                        st.markdown(
                            f"<div class='history-item'><strong>{item['timestamp']} - {item['computer']}:</strong><br>{msg_html}</div>",
                            unsafe_allow_html=True
                        )

                    # Close our custom card container
                    st.markdown("</div>", unsafe_allow_html=True)

        else:
            st.info("No active entries found.")
    
    # === TAB 2: SEARCH CLIENTS ===
    with tab_search:
        st.markdown('<h2 class="section-header">Search Clients</h2>', unsafe_allow_html=True)
        st.markdown('<div class="info-message">Search for clients by name.</div>', unsafe_allow_html=True)
        
        search_query = st.text_input("Enter client name to search", key="search_query")
        
        if st.button("Search") or st.session_state.current_search_query != search_query:
            st.session_state.current_search_query = search_query
            search_results = search_entries_by_name(st.session_state.entries, search_query)
            st.session_state.search_results = search_results
        
        if st.session_state.search_results is not None:
            if st.session_state.search_results:
                # Group results by client name
                client_groups = defaultdict(list)
                for uid, entry in st.session_state.search_results:
                    client_groups[entry['name']].append((uid, entry))
                
                for cname, entries_list in client_groups.items():
                    count_completed = sum(1 for uid, entry in entries_list if entry.get('completed', False))
                    count_active = len(entries_list) - count_completed
                    first_uid = entries_list[0][0]
                    
                    st.markdown(f"<h2 class='card-title'>{cname} (ID: {first_uid})</h2>", unsafe_allow_html=True)
                    st.markdown(f"**Total Completed Cases:** {count_completed} | **Total Ongoing Cases:** {count_active}")
                    
                    for uid, entry in entries_list:
                        with st.expander(f"Details for Case {uid}", expanded=False):
                            if entry.get('completed', False):
                                # Completed => use st.download_button for immediate download
                                filename, content = create_completion_file(uid, entry['name'], entry['history'])
                                if filename and content:
                                    st.download_button(
                                        label="Download History",
                                        data=content,
                                        file_name=filename,
                                        mime="text/plain"
                                    )
                            else:
                                # Show update form
                                st.markdown("<hr>", unsafe_allow_html=True)
                                new_message = st.text_area(
                                    f"Add Update for {entry['name']}",
                                    height=100,
                                    key=f"search_update_{uid}"
                                )
                                
                                st.markdown("<div class='payment-section'>Make Payment:</div>", unsafe_allow_html=True)
                                payment_made = st.text_input("Payment Amount (₹)", key=f"search_payment_{uid}")
                                
                                col1, col2 = st.columns([1, 1])
                                with col1:
                                    if st.button("Submit Update", key=f"search_btn_update_{uid}"):
                                        success, message = update_entry(
                                            unique_id=uid,
                                            message=new_message,
                                            computer_name=st.session_state.network.hostname,
                                            employee_name=st.session_state.network.hostname,
                                            payment_made=payment_made
                                        )
                                        st.session_state.action_status = (success, message)
                                        st.session_state.form_submitted = True
                                with col2:
                                    if st.button("Mark Completed", key=f"search_btn_complete_{uid}"):
                                        employee_name = st.session_state.network.hostname
                                        success, message = mark_entry_completed(
                                            unique_id=uid,
                                            computer_name=st.session_state.network.hostname,
                                            employee_name=employee_name
                                        )
                                        st.session_state.action_status = (success, message)
                                        st.session_state.form_submitted = True
                            
                            st.markdown("<hr>", unsafe_allow_html=True)
                            st.markdown("### History")
                            for item in reversed(entry['history']):
                                msg_html = item['message'].replace("\n", "<br>")
                                st.markdown(
                                    f"<div class='history-item'><strong>{item['timestamp']} - {item['computer']}:</strong><br>{msg_html}</div>",
                                    unsafe_allow_html=True
                                )
            else:
                st.markdown('<div class="search-no-results">No clients found matching your search.</div>', unsafe_allow_html=True)
    
    # === TAB 3: SHARED WHITEBOARD ===
    with tab_whiteboard:
        st.markdown('<h2 class="section-header">Shared Whiteboard</h2>', unsafe_allow_html=True)
        st.markdown('<div class="whiteboard-note">Use this shared space for notes, reminders, and announcements. Changes are synchronized with all connected computers.</div>', unsafe_allow_html=True)
        
        whiteboard_content = st.text_area("Team Whiteboard", value=st.session_state.whiteboard, height=400)
        
        if st.button("Update Whiteboard"):
            if whiteboard_content != st.session_state.whiteboard:
                save_whiteboard(whiteboard_content)
                st.session_state.whiteboard = whiteboard_content
                st.session_state.network.broadcast_whiteboard(whiteboard_content)
                st.success("Whiteboard updated and synchronized")
                st.session_state.notification_system.send_notification(
                    "whiteboard_update", 
                    f"Whiteboard updated by {st.session_state.network.hostname}"
                )
            else:
                st.info("No changes detected")

if __name__ == "__main__":
    main()
