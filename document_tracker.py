import streamlit as st
import json, os, socket, threading, time, difflib, base64, logging
from datetime import datetime, timedelta
from collections import defaultdict
import queue
import time
import sys

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
        with open(ENTRIES_FILE, "r", encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            else:
                logging.error("Invalid entries format. Expected a dictionary, initializing empty entries.")
                return {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_entries(entries):
    with open(ENTRIES_FILE, "w",  encoding='utf-8') as f:
        json.dump(entries, f, indent=2)

def load_whiteboard():
    try:
        with open(WHITEBOARD_FILE, "r",  encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ""

def save_whiteboard(content):
    with open(WHITEBOARD_FILE, "w", encoding='utf-8') as f:
        f.write(content)
    log_action("update_whiteboard", st.session_state.network.hostname, {"content_length": len(content)})

def is_deadline_approaching(deadline_str):
    if not deadline_str:
        return False
    try:
        deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d")
        return timedelta(0) < (deadline_date - datetime.now()) <= timedelta(hours=48)
    except ValueError:
        return False

def get_base_dir():
    if getattr(sys, 'frozen', False):
        # If running as a bundled executable, use the executable's directory.
        return os.path.dirname(sys.executable)
    else:
        # Otherwise, use the script's directory.
        return os.path.dirname(os.path.abspath(__file__))

def setup_logging():
    base_dir = get_base_dir()
    logs_dir = os.path.join(base_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(logs_dir, f"trace_{today}.log")
    
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.info("Trace started")
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
        self.entry_version_map = {}  # Track entry versions
        self.last_sync_times = {}  # Track last sync time with each peer
        
        # Start network threads
        for target in (self.discovery_sender, self.discovery_listener, self.broadcast_listener):
            threading.Thread(target=target, daemon=True).start()
        
        # Start periodic sync thread
        threading.Thread(target=self.periodic_sync, daemon=True).start()

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
                    data, addr = listener.recvfrom(1024)
                    msg = data.decode()
                    if msg.startswith("DISCOVERY:"):
                        peer_ip = msg.split(":")[1]
                        if peer_ip != self.ip and peer_ip not in self.peers:
                            self.peers.add(peer_ip)
                            self.last_sync_times[peer_ip] = 0  # Initialize sync time
                            logging.info(f"Discovered new peer: {peer_ip}")
                            # When a new peer is discovered, immediately sync entries
                            self.sync_with_peer(peer_ip)
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
                    client, addr = server.accept()
                    threading.Thread(target=self.handle_client_connection, 
                                    args=(client, addr[0]), 
                                    daemon=True).start()
                except Exception as e:
                    logging.error(f"Broadcast listener error: {e}")
        except Exception as e:
            logging.error(f"Failed to bind broadcast listener: {e}")
    
    
    def handle_client_connection(self, client_socket, peer_ip):
        """Handle incoming client connections in separate threads."""
        try:
            data = b""
            while (chunk := client_socket.recv(4096)):
                data += chunk
                # If we received less than the buffer size, assume the message is complete.
                if len(chunk) < 4096:
                    break

            if data:
                payload = json.loads(data.decode())
                if payload.get('type') == 'entries':
                    updated = self.process_received_entries(payload['data'], peer_ip)
                    if updated and 'notification_system' in st.session_state:
                        st.session_state.notification_system.send_notification(
                            "sync_update", "Entries updated from peer sync"
                        )
                    # Send an acknowledgment back to the peer.
                    response = json.dumps({
                        'type': 'sync_ack',
                        'status': 'success' if updated else 'no_change'
                    }).encode()
                    client_socket.sendall(response)

                elif payload.get('type') == 'whiteboard':
                    self.process_received_whiteboard(payload['data'])

                elif payload.get('type') == 'sync_request':
                    from datetime import datetime
                    fmt = "%d-%m-%Y %H:%M:%S"  # The timestamp format used in history entries.

                    peer_version_map = payload.get('version_map', {})
                    local_entries = load_entries()
                    entries_to_send = {}

                    for uid, local_entry in local_entries.items():
                        # Only process entries that have a history.
                        if not local_entry.get('history'):
                            continue

                        # Get the latest update from local history.
                        local_latest = max(local_entry['history'], key=lambda h: datetime.strptime(h['timestamp'], fmt))
                        local_last_modified = datetime.strptime(local_latest['timestamp'], fmt)

                        # Get the peer's version timestamp for this entry, if any.
                        peer_ts_str = peer_version_map.get(uid, {}).get('last_modified')
                        if peer_ts_str:
                            try:
                                peer_last_modified = datetime.strptime(peer_ts_str, fmt)
                            except Exception:
                                # If parsing fails, treat as no valid data.
                                peer_last_modified = None
                        else:
                            peer_last_modified = None

                        if not peer_last_modified:
                            # Peer has no data for this entry; send our latest update.
                            entries_to_send[uid] = {'latest_update': local_latest}
                        else:
                            if local_last_modified > peer_last_modified:
                                # Our update is more recent; send it.
                                entries_to_send[uid] = {'latest_update': local_latest}
                            elif local_last_modified < peer_last_modified:
                                # Peer is ahead; do not send any data.
                                continue
                            else:
                                # Conflict: timestamps are equal but data might differ.
                                # Send our update and mark it as a conflict.
                                entries_to_send[uid] = {'latest_update': local_latest, 'conflict': True}

                    response = json.dumps({
                        'type': 'entries',
                        'data': entries_to_send,
                        'version_map': self.entry_version_map
                    }).encode()
                    client_socket.sendall(response)

        except Exception as e:
            logging.error(f"Error handling client connection from {peer_ip}: {e}")
        finally:
            client_socket.close()

    def initialize_version_map(self):
        """Initialize version map from current entries"""
        entries = load_entries()
        with self.entries_lock:
            for uid, entry in entries.items():
                # Use the number of history items or completion status as version indicator
                history_len = len(entry.get('history', []))
                completion_status = 1 if entry.get('completed', False) else 0
                self.entry_version_map[uid] = {
                    'history_len': history_len,
                    'completed': completion_status,
                    'last_modified': entry.get('last_modified', datetime.now().timestamp())
                }

    def process_received_entries(self, received_entries, peer_ip=None):
        """
        Process entries received from peers with conflict resolution.
        Returns True if local entries were updated.
        """
        with self.entries_lock:
            local_entries = load_entries()
            received_version_map = received_entries.pop('version_map', {})
            updated = False

            for uid, recv in received_entries.items():
                # Track conflict resolution decisions for logging
                resolution_log = []
                
                if uid not in local_entries:
                    # New entry we don't have - just add it
                    local_entries[uid] = recv
                    self.update_version_for_entry(uid, recv)
                    resolution_log.append("Added new entry")
                    updated = True
                    continue
                
                local = local_entries[uid]
                
                # Handle completion status - "completed" is a terminal state
                if recv.get('completed') and not local.get('completed'):
                    local['completed'] = True
                    resolution_log.append("Updated completion status to completed")
                    updated = True
                
                # Check priority updates
                if 'priority' in recv and recv['priority'] != local.get('priority'):
                    # Use most recent priority setting
                    recv_time = recv.get('last_priority_update', 0)
                    local_time = local.get('last_priority_update', 0)
                    
                    if recv_time > local_time:
                        local['priority'] = recv['priority']
                        local['last_priority_update'] = recv_time
                        resolution_log.append(f"Updated priority to {recv['priority']}")
                        updated = True
                
                # Handle history entries, which might need merging
                recv_timestamps = {h['timestamp'] for h in recv.get('history', [])}
                local_timestamps = {h['timestamp'] for h in local.get('history', [])}
                
                # Find history entries we don't have
                new_timestamps = recv_timestamps - local_timestamps
                if new_timestamps:
                    for h in recv.get('history', []):
                        if h['timestamp'] in new_timestamps:
                            local.setdefault('history', []).append(h)
                            resolution_log.append(f"Added history item from {h['computer']}")
                    
                    # Re-sort history by timestamp
                    local['history'].sort(key=lambda x: x['timestamp'])
                    updated = True
                
                # Handle deadline updates
                if 'deadline' in recv and recv.get('deadline') != local.get('deadline'):
                    # Use the most recent deadline change
                    recv_deadline_time = recv.get('deadline_updated', 0)
                    local_deadline_time = local.get('deadline_updated', 0)
                    
                    if recv_deadline_time > local_deadline_time:
                        local['deadline'] = recv['deadline']
                        local['deadline_updated'] = recv_deadline_time
                        resolution_log.append(f"Updated deadline to {recv['deadline']}")
                        updated = True
                
                # Update version info for this entry
                if updated:
                    self.update_version_for_entry(uid, local)
                    # Log the resolution decisions
                    if resolution_log:
                        logging.info(f"Entry {uid} updates from {peer_ip}: {', '.join(resolution_log)}")
            
            if updated:
                # Reload entries to get any changes made by other threads
                current_entries = load_entries()
                
                # Merge our changes with current state
                for uid, entry in local_entries.items():
                    if uid in current_entries and entry != current_entries[uid]:
                        # Only update entries we've modified
                        current_entries[uid] = entry
                
                # Save merged result
                save_entries(current_entries)
                st.session_state.entries = current_entries
        
        return updated

    def update_version_for_entry(self, uid, entry):
        """Update version tracking with more consistent fields"""
        # Create a consistent structure for all entries
        history_len = len(entry.get('history', []))
        
        # Get the latest timestamp from history if available
        latest_timestamp = None
        if entry.get('history'):
            try:
                fmt = "%d-%m-%Y %H:%M:%S"
                timestamps = [datetime.strptime(h['timestamp'], fmt) for h in entry['history']]
                latest_timestamp = max(timestamps).strftime(fmt)
            except Exception as e:
                logging.error(f"Error parsing timestamps for {uid}: {e}")
        
        self.entry_version_map[uid] = {
            'history_len': history_len,
            'completed': 1 if entry.get('completed', False) else 0,
            'last_modified': latest_timestamp or datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            'checksum': self._calculate_entry_checksum(entry)  # Add checksum for better conflict detection
        }

    def _calculate_entry_checksum(self, entry):
        """Calculate a simple checksum of entry content for conflict detection"""
        import hashlib
        content = json.dumps(entry, sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()

    def broadcast_entries(self, entries_to_broadcast=None):
        """
        Broadcast entries to all peers.
        If entries_to_broadcast is None, broadcast all entries.
        """
        if not self.peers:
            logging.warning("No peers to broadcast entries to")
            return
        
        if entries_to_broadcast is None:
            # Broadcast all entries
            entries = load_entries()
        else:
            entries = entries_to_broadcast
            
        # Include version map
        entries_with_metadata = entries.copy()
        entries_with_metadata['version_map'] = self.entry_version_map
        
        payload = json.dumps({
            'type': 'entries', 
            'data': entries_with_metadata
        }).encode()
        
        threading.Thread(target=self._broadcast_task, 
                        args=(payload, 'entries'), 
                        daemon=True).start()

    def broadcast_entry_update(self, entry_id, updated_entry):
        """
        Broadcast a single entry update to all peers.
        """
        if not self.peers:
            logging.warning("No peers to broadcast entry update to")
            return
        
        # Update version info for this entry
        self.update_version_for_entry(entry_id, updated_entry)
        
        # Create a payload with just this entry
        entries_payload = {entry_id: updated_entry}
        
        # Include version map
        entries_payload['version_map'] = {
            entry_id: self.entry_version_map.get(entry_id, {})
        }
        
        payload = json.dumps({
            'type': 'entries', 
            'data': entries_payload
        }).encode()
        
        threading.Thread(target=self._broadcast_task, 
                        args=(payload, 'entry_update'), 
                        daemon=True).start()
        
        logging.info(f"Broadcasting update for entry {entry_id}")

    def mark_entry_completed(self, entry_id):
        """
        Mark an entry as completed and broadcast the update.
        """
        with self.entries_lock:
            entries = load_entries()
            if entry_id in entries:
                entries[entry_id]['completed'] = True
                entries[entry_id]['completion_time'] = datetime.now().timestamp()
                save_entries(entries)
                
                # Update session state
                st.session_state.entries = entries
                
                # Broadcast the update
                self.broadcast_entry_update(entry_id, entries[entry_id])
                
                # Create completion file
                create_completion_file(
                    entry_id, 
                    entries[entry_id].get('name', 'Unnamed'), 
                    entries[entry_id].get('history', [])
                )
                
                logging.info(f"Entry {entry_id} marked as completed")
                return True
            return False

    def update_entry(self, entry_id, updates):
        """
        Update an entry with the provided updates and broadcast the change.
        """
        with self.entries_lock:
            entries = load_entries()
            if entry_id in entries:
                # Apply updates
                for key, value in updates.items():
                    if key == 'history':
                        # For history, append rather than replace
                        entries[entry_id].setdefault('history', []).extend(value)
                    else:
                        entries[entry_id][key] = value
                
                # Add last_modified timestamp
                entries[entry_id]['last_modified'] = datetime.now().timestamp()
                
                # Save updates
                save_entries(entries)
                
                # Update session state
                st.session_state.entries = entries
                
                # Broadcast the update
                self.broadcast_entry_update(entry_id, entries[entry_id])
                
                logging.info(f"Entry {entry_id} updated with {len(updates)} changes")
                return True
            return False

    def add_entry_message(self, entry_id, message, computer=None):
        """
        Add a message to an entry's history and broadcast the update.
        """
        if not computer:
            computer = self.hostname
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        history_item = {
            "timestamp": timestamp,
            "computer": computer,
            "message": message
        }
        
        return self.update_entry(entry_id, {
            'history': [history_item]
        })

    def merge_entries(self, local_entry, remote_entry, uid):
        """More sophisticated entry merging logic"""
        # Track what changed for logging
        changes = []
        merged_entry = local_entry.copy()
        
        # Always preserve completed status (once completed, stays completed)
        if remote_entry.get('completed') and not local_entry.get('completed'):
            merged_entry['completed'] = True
            changes.append("completed status")
        
        # Merge history entries with duplicate detection
        local_history = {h['timestamp']: h for h in local_entry.get('history', [])}
        for h in remote_entry.get('history', []):
            if h['timestamp'] not in local_history:
                merged_entry.setdefault('history', []).append(h)
                changes.append(f"history item from {h['computer']}")
        
        # Sort history by timestamp
        if 'history' in merged_entry:
            merged_entry['history'].sort(key=lambda x: x['timestamp'])
        
        # Priority - use highest priority between the two
        priority_order = {p: i for i, p in enumerate(PRIORITY_LEVELS)}
        local_priority = local_entry.get('priority')
        remote_priority = remote_entry.get('priority')
        
        if local_priority and remote_priority:
            if priority_order.get(remote_priority, 0) > priority_order.get(local_priority, 0):
                merged_entry['priority'] = remote_priority
                changes.append(f"priority to {remote_priority}")
        
        # Handle deadline changes
        local_deadline = local_entry.get('deadline')
        remote_deadline = remote_entry.get('deadline')
        
        if remote_deadline and (not local_deadline or 
                            (remote_entry.get('deadline_updated', 0) > 
                                local_entry.get('deadline_updated', 0))):
            merged_entry['deadline'] = remote_deadline
            merged_entry['deadline_updated'] = remote_entry.get('deadline_updated', time.time())
            changes.append(f"deadline to {remote_deadline}")
        
        # Handle payment merges carefully - use most recent payment data, or combine if necessary
        if 'total_paid' in remote_entry and 'total_paid' in local_entry:
            try:
                remote_paid = float(remote_entry['total_paid'])
                local_paid = float(local_entry['total_paid'])
                
                # If remote is higher and there's a difference, take the remote value
                if remote_paid > local_paid:
                    merged_entry['total_paid'] = remote_entry['total_paid']
                    
                    # Recalculate remaining amount
                    if 'total_payable' in merged_entry:
                        total = float(merged_entry['total_payable'])
                        merged_entry['remaining'] = str(max(0, total - remote_paid))
                    
                    changes.append(f"payment amount to {remote_entry['total_paid']}")
            except ValueError:
                logging.error(f"Payment merge error for {uid}: Invalid number format")
    
        # Return merged entry and list of changes
        return merged_entry, changes

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

    def sync_with_peer(self, peer_ip):
        """
        Initiate a sync with a specific peer.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(5)
                client.connect((peer_ip, BROADCAST_PORT))
                
                # Send sync request
                sync_request = json.dumps({
                    'type': 'sync_request',
                    'source': self.ip,
                    'version_map': self.entry_version_map
                }).encode()
                
                client.sendall(sync_request)
                
                # Wait for response
                data = b""
                while True:
                    try:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    except socket.timeout:
                        break
                
                if data:
                    payload = json.loads(data.decode())
                    if payload.get('type') == 'entries':
                        updated = self.process_received_entries(payload['data'], peer_ip)
                        if updated and 'notification_system' in st.session_state:
                            st.session_state.notification_system.send_notification(
                                "sync_update", f"Entries synchronized with {peer_ip}"
                            )
                        
                        # Update last sync time
                        self.last_sync_times[peer_ip] = time.time()
                        logging.info(f"Successfully synced with peer {peer_ip}")
                        return True
            
            return False
        except Exception as e:
            logging.error(f"Failed to sync with peer {peer_ip}: {e}")
            # Remove peer if unreachable after multiple attempts
            if peer_ip in self.peers:
                self.peers.remove(peer_ip)
                logging.warning(f"Removed unreachable peer {peer_ip}")
            return False

    def periodic_sync(self):
        """
        Periodically sync with all peers to ensure consistency.
        """
        # First initialize our version map
        self.initialize_version_map()
        
        while self.running:
            try:
                # Sleep at the beginning to allow initial discovery
                time.sleep(60)  # Sync every minute
                
                if not self.peers:
                    continue
                    
                current_time = time.time()
                for peer_ip in list(self.peers):  # Use list to avoid modification during iteration
                    # Check if we need to sync (>5 minutes since last sync)
                    last_sync = self.last_sync_times.get(peer_ip, 0)
                    if current_time - last_sync > 300:  # 5 minutes
                        sync_success = self.sync_with_peer(peer_ip)
                        if not sync_success and current_time - last_sync > 1800:  # 30 minutes
                            # If peer hasn't responded for 30 minutes, remove it
                            if peer_ip in self.peers:
                                self.peers.remove(peer_ip)
                                logging.warning(f"Removed unresponsive peer {peer_ip}")
            except Exception as e:
                logging.error(f"Error in periodic sync: {e}")
                time.sleep(30)  # Sleep and try again

    def process_received_whiteboard(self, received_content):
        with self.whiteboard_lock:
            local_content = load_whiteboard()
            if received_content != local_content:
                save_whiteboard(received_content)
                st.session_state.whiteboard = received_content
                logging.info("Whiteboard updated from broadcasted content")

    def _broadcast_task(self, payload_json, task_type):
        for peer_ip in list(self.peers):  # Use list to avoid modification during iteration
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                    client.settimeout(5)  # Increased timeout for larger data
                    client.connect((peer_ip, BROADCAST_PORT))
                    client.sendall(payload_json)
                logging.debug(f"Broadcast {task_type} to {peer_ip} successful")
            except Exception as e:
                logging.error(f"Failed to broadcast {task_type} to {peer_ip}: {e}")
                # If connection fails, consider removing peer after multiple failures
                # This is handled in periodic_sync

    def _send_with_retry(self, peer_ip, port, data, max_retries=3):
        """Send data to a peer with retry logic"""
        for attempt in range(max_retries):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                    client.settimeout(5)  # 5 second timeout
                    client.connect((peer_ip, port))
                    client.sendall(data)
                    
                    # Wait for acknowledgement
                    response = b""
                    start_time = time.time()
                    while time.time() - start_time < 10:  # 10 second max wait
                        try:
                            chunk = client.recv(4096)
                            if not chunk:
                                break
                            response += chunk
                        except socket.timeout:
                            continue
                    
                    if response:
                        return True, response
                    
                logging.warning(f"No response from {peer_ip} (attempt {attempt+1}/{max_retries})")
            except (socket.timeout, ConnectionRefusedError):
                logging.warning(f"Connection timeout to {peer_ip} (attempt {attempt+1}/{max_retries})")
            except Exception as e:
                logging.error(f"Error sending to {peer_ip}: {e}")
                break  # Don't retry on non-timeout errors
                
            # Exponential backoff
            time.sleep(0.5 * (2 ** attempt))
        
        return False, None

    def sync_check_for_updates(self, peer_ip):
        """Check for updates from a peer using checksums"""
        local_entries = self.entries_manager.get_all_entries()
        
        # Build checksum map of all our entries
        checksum_map = {}
        for uid, entry in local_entries.items():
            checksum_map[uid] = self._calculate_entry_checksum(entry)
        
        # Send our checksums to peer
        request = {
            'type': 'checksum_sync',
            'source': self.ip,
            'checksums': checksum_map
        }
        
        success, response_data = self._send_with_retry(
            peer_ip, BROADCAST_PORT, json.dumps(request).encode()
        )
        
        if success and response_data:
            try:
                response = json.loads(response_data.decode())
                if response.get('type') == 'checksum_diff':
                    # Process entries that differ by checksum
                    diff_entries = response.get('entries', {})
                    if diff_entries:
                        self.process_received_entries(diff_entries, peer_ip)
                        logging.info(f"Updated {len(diff_entries)} entries from {peer_ip}")
                        return True
            except Exception as e:
                logging.error(f"Error in checksum sync: {e}")
        
        return False

class EntriesManager:
    def __init__(self):
        self.entries_cache = {}
        self.entries_lock = threading.RLock()  # Reentrant lock
        self.last_load_time = 0
        self.last_save_time = 0
        self.dirty = False
        self.load_entries()  # Initial load
        
        # Start background save thread
        threading.Thread(target=self._periodic_save, daemon=True).start()
    
    def load_entries(self, force=False):
        """Load entries from file with caching"""
        now = time.time()
        
        with self.entries_lock:
            # Only reload if forced or file appears newer than our cache
            try:
                file_mtime = os.path.getmtime(ENTRIES_FILE)
                if force or file_mtime > self.last_load_time:
                    with open(ENTRIES_FILE, "r", encoding='utf-8') as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self.entries_cache = data
                            self.last_load_time = now
                            logging.info(f"Entries loaded from file ({len(data)} entries)")
                        else:
                            logging.error("Invalid entries format in file")
            except FileNotFoundError:
                self.entries_cache = {}
                self.last_load_time = now
            except json.JSONDecodeError:
                logging.error("JSON decode error when loading entries")
            except Exception as e:
                logging.error(f"Error loading entries: {e}")
                
        return self.entries_cache
    
    def save_entries(self, force=False):
        """Save entries to file if dirty or forced"""
        if not self.dirty and not force:
            return
            
        with self.entries_lock:
            try:
                with open(ENTRIES_FILE, "w", encoding='utf-8') as f:
                    json.dump(self.entries_cache, f, indent=2)
                self.last_save_time = time.time()
                self.dirty = False
                logging.debug(f"Entries saved to file ({len(self.entries_cache)} entries)")
            except Exception as e:
                logging.error(f"Error saving entries: {e}")
    
    def get_entry(self, uid):
        """Get a specific entry with reloading if needed"""
        with self.entries_lock:
            # Check if we should reload from disk
            if time.time() - self.last_load_time > 30:  # Reload every 30 seconds max
                self.load_entries()
                
            return self.entries_cache.get(uid)
    
    def get_all_entries(self):
        """Get all entries with reloading if needed"""
        with self.entries_lock:
            # Check if we should reload from disk
            if time.time() - self.last_load_time > 30:  # Reload every 30 seconds max
                self.load_entries()
                
            return self.entries_cache.copy()
    
    def update_entry(self, uid, entry):
        """Update a specific entry"""
        with self.entries_lock:
            self.entries_cache[uid] = entry
            self.dirty = True
            
    def update_entries(self, entries_dict):
        """Update multiple entries at once"""
        with self.entries_lock:
            self.entries_cache.update(entries_dict)
            self.dirty = True
    
    def _periodic_save(self):
        """Periodically save entries to disk"""
        while True:
            time.sleep(5)  # Check every 5 seconds
            try:
                if self.dirty and time.time() - self.last_save_time > 5:
                    self.save_entries()
            except Exception as e:
                logging.error(f"Error in periodic save: {e}")

class NotificationSystem:
    def __init__(self, network):
        self.network = network
        self.hostname = network.hostname
        self.ip = network.ip
        self.running = True
        self.notification_queue = queue.Queue()
        self.notification_history = []  # Store recent notifications to prevent duplicates
        self.max_history = 50  # Maximum number of recent notifications to track
        self.notification_lock = threading.Lock()
        
        # Start the notification threads
        threading.Thread(target=self.notification_listener, daemon=True).start()
        threading.Thread(target=self.notification_processor, daemon=True).start()
        
        logging.info(f"Notification system initialized on {self.hostname} ({self.ip})")

    def notification_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(('', NOTIFICATION_PORT))
            logging.info(f"Notification listener started on port {NOTIFICATION_PORT}")
            while self.running:
                try:
                    listener.settimeout(1.0)  # Allow checking self.running periodically
                    data, _ = listener.recvfrom(1024)
                    notif = json.loads(data.decode())
                    self._handle_notification(notif)
                except socket.timeout:
                    continue
                except json.JSONDecodeError:
                    logging.warning("Received invalid notification format")
                except Exception as e:
                    logging.error(f"Notification listener error: {e}")
        except Exception as e:
            logging.error(f"Failed to bind notification listener: {e}")
            
    def notification_processor(self):
        """Process notifications from queue and send to peers"""
        while self.running:
            try:
                # Get the next notification from the queue with a timeout
                try:
                    notification = self.notification_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                # Generate a notification ID for deduplication
                notification_id = f"{notification['source']}:{notification['timestamp']}:{notification['type']}"
                notification['id'] = notification_id
                
                # Send to all peers
                self._send_to_all_peers(notification)
                
                # Mark as done
                self.notification_queue.task_done()
            except Exception as e:
                logging.error(f"Error in notification processor: {e}")

    def _handle_notification(self, notification):
        """Handle incoming notifications from peers"""
        # Check for duplicates using notification ID
        notification_id = notification.get('id')
        if notification_id:
            with self.notification_lock:
                # Skip if we've seen this notification before
                if notification_id in [n.get('id') for n in self.notification_history]:
                    return
                
                # Add to history for deduplication
                self.notification_history.append(notification)
                if len(self.notification_history) > self.max_history:
                    self.notification_history.pop(0)  # Remove oldest

        # Get notification details
        notif_type = notification.get('type', 'unknown')
        message = notification.get('message', 'No message')
        source = notification.get('source', 'Unknown')
        formatted_message = f"{source}: {message}"
        
        # Log the notification
        logging.info(f"Notification: [{notif_type}] {formatted_message}")
        
        # Display notification in the UI based on type
        try:
            if notif_type in ['new_entry', 'entry_update', 'whiteboard_update']:
                st.info(formatted_message)
            elif notif_type == 'entry_completed':
                st.success(formatted_message)
            elif notif_type in ['priority_update', 'deadline_update']:
                st.warning(formatted_message)
            elif notif_type == 'sync_update':
                st.info(formatted_message)
            else:
                st.info(formatted_message)
                
            # Update UI state if needed
            if notif_type == 'reload_entries' and 'entries' in st.session_state:
                st.session_state.entries = load_entries()
                st.experimental_rerun()
        except Exception as e:
            logging.error(f"Error displaying notification: {e}")

    def send_notification(self, notification_type, message, details=None):
        """Queue a notification to be sent to all peers"""
        notification = {
            'type': notification_type,
            'message': message,
            'source': self.hostname,
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'details': details or {}
        }
        
        # Generate a notification ID for deduplication
        notification_id = f"{notification['source']}:{notification['timestamp']}:{notification['type']}"
        notification['id'] = notification_id
        
        # Add to local history first
        with self.notification_lock:
            self.notification_history.append(notification)
            if len(self.notification_history) > self.max_history:
                self.notification_history.pop(0)  # Remove oldest
        
        # Display locally first
        self._handle_notification(notification)
        
        # Queue for sending to peers
        self.notification_queue.put(notification)
        logging.info(f"Notification queued: {notification_type} - {message}")

    def _send_to_all_peers(self, notification):
        """Send notification to all peers with retry logic"""
        payload = json.dumps(notification).encode()
        max_retries = 2
        
        for peer_ip in list(self.network.peers):
            retries = 0
            while retries <= max_retries:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                        sock.settimeout(1)
                        sock.sendto(payload, (peer_ip, NOTIFICATION_PORT))
                    logging.debug(f"Notification sent to {peer_ip}")
                    break  # Success, exit retry loop
                except socket.timeout:
                    logging.warning(f"Timeout sending notification to {peer_ip} (attempt {retries+1})")
                    retries += 1
                except Exception as e:
                    logging.error(f"Failed to send notification to {peer_ip}: {e}")
                    break  # Don't retry on non-timeout errors
            
            # If we exhausted retries, consider removing the peer
            if retries > max_retries:
                logging.warning(f"Peer {peer_ip} is unresponsive for notifications")

    def notify_entry_created(self, entry_id, entry_name):
        """Notification for a new entry"""
        self.send_notification(
            "new_entry",
            f"New entry created: {entry_name}",
            {"entry_id": entry_id, "entry_name": entry_name}
        )

    def notify_entry_updated(self, entry_id, entry_name, update_type="general"):
        """Notification for an entry update"""
        self.send_notification(
            "entry_update",
            f"Entry updated: {entry_name} ({update_type})",
            {"entry_id": entry_id, "entry_name": entry_name, "update_type": update_type}
        )

    def notify_entry_completed(self, entry_id, entry_name):
        """Notification for a completed entry"""
        self.send_notification(
            "entry_completed",
            f"Entry completed: {entry_name}",
            {"entry_id": entry_id, "entry_name": entry_name}
        )

    def notify_priority_changed(self, entry_id, entry_name, new_priority):
        """Notification for a priority change"""
        self.send_notification(
            "priority_update",
            f"Priority changed to {new_priority} for: {entry_name}",
            {"entry_id": entry_id, "entry_name": entry_name, "priority": new_priority}
        )

    def notify_deadline_changed(self, entry_id, entry_name, new_deadline):
        """Notification for a deadline change"""
        self.send_notification(
            "deadline_update",
            f"Deadline updated to {new_deadline} for: {entry_name}",
            {"entry_id": entry_id, "entry_name": entry_name, "deadline": new_deadline}
        )

    def notify_whiteboard_updated(self):
        """Notification for whiteboard updates"""
        self.send_notification(
            "whiteboard_update",
            f"Whiteboard updated by {self.hostname}",
            {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        )

    def stop(self):
        """Stop the notification system"""
        self.running = False
        logging.info("Notification system stopped")

def update_entry_priority(entries):
    updated = False
    # If entries is a list of (uid, entry_dict) tuples
    if isinstance(entries, list):
        for i, (uid, entry_dict) in enumerate(entries):
            if entry_dict.get('completed', False):
                continue
            deadline_str = entry_dict.get('deadline')
            if deadline_str and is_deadline_approaching(deadline_str):
                if entry_dict.get('priority') != "Highest":
                    entry_dict['priority'] = "Highest"
                    updated = True
                    log_action("priority_update", "system", {
                        "unique_id": uid, 
                        "client_name": entry_dict['name'],
                        "reason": "Deadline approaching"
                    })
    # If entries is a dictionary with UIDs as keys
    elif isinstance(entries, dict):
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
    results = []
    
    # If entries is a list of dictionaries
    if isinstance(entries, list):
        for entry in entries:
            if name_query in entry['name'].lower():
                # If you need a unique ID for each entry, you might need to 
                # extract it from the entry or generate one
                results.append((entry.get('id', ''), entry))
    
    # If entries is a dictionary with UIDs as keys
    elif isinstance(entries, dict):
        results = [
            (uid, entry) for uid, entry in entries.items()
            if name_query in entry['name'].lower()
        ]
    
    return results

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
    return True, "Entry marked as completed" 

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
        background-color: #1e1a1a;
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

    if 'active_tab' not in st.session_state:
        st.session_state.active_tab = "Client Entries"

    # Initialize font size if not set
    if "font_size" not in st.session_state:
        st.session_state.font_size = 1.1

    # Font size controls in sidebar
    st.sidebar.markdown("<h2 class='section-header'>Settings</h2>", unsafe_allow_html=True)
    col_fs1, col_fs2, col_fs3 = st.sidebar.columns([1, 1, 2])
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
    st.markdown('<h1 class="main-header">Trace</h1>', unsafe_allow_html=True)

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

    # Periodically reload entries and whiteboard, and update entry priorities
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

    if st.session_state.action_status:
        success, message = st.session_state.action_status
        if success:
            st.success(message)
        else:
            st.error(message)
        st.session_state.action_status = None

    active_peers = [
        peer for peer, last_sync in st.session_state.network.last_sync_times.items() 
        if time.time() - last_sync < 600  # 10 minutes threshold
    ]
    
    st.sidebar.markdown('<h2 class="section-header">Network Status</h2>', unsafe_allow_html=True)
    st.sidebar.markdown(
        f'<div class="network-status">Host: {st.session_state.network.hostname}<br>'
        f'IP: {st.session_state.network.ip}<br>'
        f'Connected Peers: {len(active_peers)}</div>',
        unsafe_allow_html=True
    )

    if st.sidebar.button("🔄 Refresh & Sync"):
        # Refresh local data and clear peers
        st.session_state.network.peers = set()
        st.session_state.entries = load_entries()
        st.session_state.whiteboard = load_whiteboard()
        
        # Perform checksum sync for all peers
        checksum_sync_results = []
        for peer in list(st.session_state.network.peers):
            try:
                # Assuming sync_check_for_updates now uses load_entries()
                result = st.session_state.network.sync_check_for_updates(peer)
                checksum_sync_results.append(f"{peer}: {'Synced' if result else 'No change'}")
            except Exception as e:
                logging.error(f"Checksum sync error with {peer}: {e}")
                checksum_sync_results.append(f"{peer}: Error")
        
        # Optionally display results (or log them)
        if checksum_sync_results:
            st.sidebar.info("Checksum Sync Results:\n" + "\n".join(checksum_sync_results))
        st.rerun()
    
    if st.session_state.active_tab in ["Client Entries", "Search Clients"]:
        if st.session_state.active_tab == "Client Entries":
            first_tab = "Client Entries"
            second_tab = "Search Clients"
        else:
            first_tab = "Search Clients"
            second_tab = "Client Entries"
        tab_main, tab_search, tab_whiteboard = st.tabs([first_tab, second_tab, "Shared Whiteboard"])
    else:
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
                st.session_state.active_tab = "Client Entries"
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
            if isinstance(entry, dict) and not entry.get('completed', False)
        }

        def priority_sort_key(item):
            uid, entry = item
            return (-PRIORITY_LEVELS.index(entry.get('priority', 'Medium')), entry['name'])

        sorted_active_entries = sorted(active_entries.items(), key=priority_sort_key)

        if sorted_active_entries:
            st.markdown(f'<h3 class="tab-header">Active Entries ({len(sorted_active_entries)})</h3>', unsafe_allow_html=True)
            
            for uid, entry in sorted_active_entries:
                with st.expander(f"{entry['name']} (ID: {uid})", expanded=False):
                    priority_class = entry['priority'].lower()

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

                    total_payable = float(entry.get('total_payable', '0'))
                    total_paid = float(entry.get('total_paid', '0'))
                    remaining = float(entry.get('remaining', '0'))

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

                    if entry.get('deadline'):
                        st.markdown(
                            f"<div class='entry-card-deadline'>Deadline: {entry['deadline']} {deadline_text}</div>",
                            unsafe_allow_html=True
                        )

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

                    new_message = st.text_area(
                        f"Add Update for {entry['name']}",
                        key=f"update_{uid}",
                        height=100,
                        placeholder="Type your update here..."
                    )
                    employee_update = st.text_input(
                        "Employee Name",
                        value=st.session_state.network.hostname,
                        key=f"update_employee_{uid}"
                    )

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

                    st.markdown("<h3>History</h3>", unsafe_allow_html=True)
                    for item in reversed(entry['history']):
                        msg_html = item['message'].replace("\n", "<br>")
                        st.markdown(
                            f"<div class='history-item'><strong>{item['timestamp']} - {item['computer']}:</strong><br>{msg_html}</div>",
                            unsafe_allow_html=True
                        )
                    st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.info("No active entries found.")
    
    # === TAB 2: SEARCH CLIENTS ===
    with tab_search:
        st.markdown('<h2 class="section-header">Search Clients</h2>', unsafe_allow_html=True)
        st.markdown('<div class="info-message">Search for clients by name.</div>', unsafe_allow_html=True)
        
        search_query = st.text_input("Enter client name to search", key="search_query")
        
        if st.button("Search") or st.session_state.current_search_query != search_query:
            st.session_state.active_tab = "Search Clients"
            st.session_state.current_search_query = search_query
            search_results = search_entries_by_name(st.session_state.entries, search_query)
            st.session_state.search_results = search_results
        
        if st.session_state.search_results is not None:
            if st.session_state.search_results:
                from collections import defaultdict
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
                            if not entry.get('completed', False):
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
                st.session_state.notification_system.notify_whiteboard_updated()
                st.success("Whiteboard updated and broadcasted.")
            else:
                st.info("No changes to update.")

if __name__ == "__main__":
    main()