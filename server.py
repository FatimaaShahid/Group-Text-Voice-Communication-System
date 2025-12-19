#!/usr/bin/env python3
"""
Unified Server supporting two modes:
- Messaging (text chat)
- Voice (raw audio streaming)

Operator commands:
- pending           : show pending connection requests
- accept <pid>      : accept pending request (pid from pending list)
- reject <pid>      : reject pending request
- list              : show connected clients (id, name, ip)
- kick <id>         : disconnect a connected client by user id
- broadcast <msg>   : send a server message to all clients
- stop              : shutdown server
"""

import socket
import threading
import time
import sys

HOST = '0.0.0.0'
PORT = 50007

# Audio defaults (used only in voice mode)
CHUNK = 2048
RATE = 48000
CHANNELS = 1

# runtime mode: set at server start
MODE = None  # "MESSAGING" or "VOICE"

server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_sock.bind((HOST, PORT))
server_sock.listen(50)

# Data structures
clients_lock = threading.Lock()
clients = {}         # conn -> {"id":int, "name":str, "addr":(ip,port)}
next_user_id = 1

pending_lock = threading.Lock()
pending = {}         # pending_id -> {"conn":conn, "name":str, "addr":addr, "event":threading.Event(), "decision":None}
next_pending_id = 1

running = True

def broadcast_message(text, exclude_conn=None):
    """Send a UTF-8 text message to all connected clients (messaging mode or notifications)."""
    msg = text.encode()
    with clients_lock:
        for conn in list(clients.keys()):
            if conn == exclude_conn:
                continue
            try:
                conn.sendall(msg)
            except Exception:
                _disconnect_conn(conn)

def broadcast_audio(data, exclude_conn=None):
    """Send raw audio bytes to all connected clients except the sender."""
    with clients_lock:
        for conn in list(clients.keys()):
            if conn == exclude_conn:
                continue
            try:
                conn.sendall(data)
            except Exception:
                _disconnect_conn(conn)

def _disconnect_conn(conn):
    """Internal: remove and close a client connection if present."""
    global clients
    with clients_lock:
        if conn in clients:
            user = clients.pop(conn)
            try:
                conn.close()
            except:
                pass
            print(f"[-] Removed: {user['name']} (ID:{user['id']}) {user['addr']}")
            # notify others
            broadcast_message(f"[Server]: {user['name']} (ID:{user['id']}) has left the session.")

def handle_accepted_client(conn, addr, user_id, name):
    """
    For accepted clients:
    - In MESSAGING mode: receive text messages and broadcast them (prefixed by name)
    - In VOICE mode: receive audio chunks and broadcast to others. Also server sends join/leave notifications.
    """
    try:
        if MODE == "VOICE":
            # Send the current list of users so client can show already-joined participants
            with clients_lock:
                users = [f"{u['id']}|{u['name']}" for u in clients.values()]
            users_msg = "[USERS] " + ",".join(users)
            try:
                conn.sendall(users_msg.encode())
            except:
                pass
            broadcast_message(f"[Server]: {name} (ID:{user_id}) joined the call.", exclude_conn=conn)
            # Now loop and relay audio bytes
            while running:
                try:
                    data = conn.recv(CHUNK)
                    if not data:
                        break
                    # detect textual leave command if it's short and decodable
                    if len(data) < 64:
                        try:
                            text = data.decode(errors='ignore').strip()
                            if text.lower() == "leave":
                                break
                        except:
                            pass
                    # broadcast raw audio to others
                    broadcast_audio(data, exclude_conn=conn)
                except ConnectionResetError:
                    break
                except Exception:
                    break

        else:  # MESSAGING
            broadcast_message(f"[Server] User joined -> ID:{user_id}, Name:{name}", exclude_conn=conn)
            # Send personal confirmation
            try:
                conn.sendall(f"Welcome {name}! Your ID is {user_id}".encode())
            except:
                pass

            while running:
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    message = data.decode(errors='ignore').strip()
                    if not message:
                        continue
                    if message.lower() == "leave":
                        break
                    full_msg = f"{name}: {message}"
                    print(full_msg)
                    broadcast_message(full_msg, exclude_conn=conn)
                except ConnectionResetError:
                    break
                except Exception:
                    break

    finally:
        # Remove client and notify
        _disconnect_conn(conn)

def handle_new_connection(conn, addr):
    """
    Initial handshake for new connections:
    Expect client to send: "NAME:<their name>" quickly.
    Then create a pending entry and wait for operator decision.
    """
    global next_pending_id, pending, next_user_id, clients
    try:
        conn.settimeout(500)  # short timeout for initial name----------------------------------------------------------------------
        raw = conn.recv(2048)
    except Exception:
        try:
            conn.close()
        except:
            pass
        return

    try:
        s = raw.decode(errors='ignore').strip()
    except:
        s = ""

    name = ""
    if s.startswith("NAME:"):
        name = s[5:].strip()
    else:
        # no proper handshake: close
        try:
            conn.sendall("REJECT".encode())
        except:
            pass
        try:
            conn.close()
        except:
            pass
        return

    # create pending entry and wait for operator accept/reject
    with pending_lock:
        pid = next_pending_id
        next_pending_id += 1
        ev = threading.Event()
        pending[pid] = {"conn": conn, "name": name, "addr": addr, "event": ev, "decision": None}
    print(f"[PENDING {pid}] {name} from {addr}. Use 'accept {pid}' or 'reject {pid}'.")

    # Wait until operator sets decision
    ev.wait()
    with pending_lock:
        info = pending.pop(pid, None)
    if not info:
        try:
            conn.close()
        except:
            pass
        return

    decision = info["decision"]
    if not decision:
        # reject
        try:
            conn.sendall("REJECT".encode())
        except:
            pass
        try:
            conn.close()
        except:
            pass
        print(f"[REJECTED] {name} from {addr}")
        return

    # accept
    with clients_lock:
        user_id = next_user_id
        next_user_id += 1
        clients[conn] = {"id": user_id, "name": name, "addr": addr}
    # send mode token as acceptance
    try:
        conn.sendall(f"MODE:{MODE}".encode())
    except:
        _disconnect_conn(conn)
        return

    print(f"[ACCEPTED] {name} -> ID:{user_id} from {addr}. Total clients: {len(clients)}")

    # spawn handler for accepted connection
    threading.Thread(target=handle_accepted_client, args=(conn, addr, user_id, name), daemon=True).start()

def accept_loop():
    """Main accept loop — spawn a thread per incoming connection (handshake/pending)."""
    while running:
        try:
            conn, addr = server_sock.accept()
            threading.Thread(target=handle_new_connection, args=(conn, addr), daemon=True).start()
        except Exception:
            break

def operator_cli():
    """Command-line for server operator to manage pending/clients."""
    global running, MODE
    help_text = (
        "Commands:\n"
        " pending                     -> show pending connection requests\n"
        " accept <pid>                -> accept pending request\n"
        " reject <pid>                -> reject pending request\n"
        " list                        -> show connected clients\n"
        " broadcast <message>         -> server send message to all\n"
        " stop                        -> shutdown server\n"
        " help                        -> show this\n"
    )
    print(help_text)
    while running:
        try:
            cmd = input("").strip()
        except EOFError:
            cmd = "stop"
        if not cmd:
            continue
        parts = cmd.split(" ", 1)
        action = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if action == "pending":
            with pending_lock:
                if not pending:
                    print("No pending requests.")
                else:
                    for pid, info in pending.items():
                        nm = info["name"]
                        addr = info["addr"]
                        print(f"PID:{pid} - {nm} from {addr}")
        elif action == "accept":
            if not arg:
                print("Usage: accept <pid>")
                continue
            try:
                pid = int(arg)
            except:
                print("invalid pid")
                continue
            with pending_lock:
                if pid not in pending:
                    print("No such pending pid")
                else:
                    pending[pid]["decision"] = True
                    pending[pid]["event"].set()
                    print(f"Accepted pending {pid}")
        elif action == "reject":
            if not arg:
                print("Usage: reject <pid>")
                continue
            try:
                pid = int(arg)
            except:
                print("invalid pid")
                continue
            with pending_lock:
                if pid not in pending:
                    print("No such pending pid")
                else:
                    pending[pid]["decision"] = False
                    pending[pid]["event"].set()
                    print(f"Rejected pending {pid}")
        elif action == "list":
            with clients_lock:
                if not clients:
                    print("No connected clients.")
                else:
                    for c, info in clients.items():
                        print(f"ID:{info['id']} Name:{info['name']} IP:{info['addr'][0]} Port:{info['addr'][1]}")
        elif action == "kick":
            if not arg:
                print("Usage: kick <id>")
                continue
            try:
                uid = int(arg)
            except:
                print("invalid id")
                continue
            kicked = False
            with clients_lock:
                for conn, info in list(clients.items()):
                    if info["id"] == uid:
                        try:
                            conn.sendall("[Server]: You were kicked by admin.".encode())
                        except:
                            pass
                        _disconnect_conn(conn)
                        kicked = True
                        break
            if kicked:
                print(f"Kicked user {uid}")
            else:
                print("No such user id")
        elif action == "broadcast":
            if not arg:
                print("Usage: broadcast <message>")
                continue
            broadcast_message(f"[Server]: {arg}")
            print("Broadcast sent.")
        elif action == "stop":
            print("Stopping server...")
            running = False
            # reject all pending
            with pending_lock:
                for pid, pinfo in list(pending.items()):
                    try:
                        pinfo["conn"].sendall("REJECT".encode())
                    except:
                        pass
                    try:
                        pinfo["conn"].close()
                    except:
                        pass
                pending.clear()
            # disconnect all clients
            with clients_lock:
                for conn in list(clients.keys()):
                    try:
                        conn.sendall("[Server]: Server shutting down.".encode())
                    except:
                        pass
                    try:
                        conn.close()
                    except:
                        pass
                clients.clear()
            try:
                server_sock.close()
            except:
                pass
            break
        elif action == "help":
            print(help_text)
        else:
            print("Unknown command. Type 'help' for commands.")

def main():
    global MODE
    print("Select server mode:\n1) Messaging (text chat)\n2) Voice (audio chat)")
    choice = input("Enter 1 or 2: ").strip()
    if choice == "1":
        MODE = "MESSAGING"
    elif choice == "2":
        # check for pyaudio presence when starting voice
        try:
            import pyaudio  # noqa: F401
        except Exception as e:
            print("PyAudio is required for voice mode. Install with: pip install pyaudio")
            print("Exiting.")
            return
        MODE = "VOICE"
    else:
        print("Invalid choice. Exiting.")
        return

    print(f"Server mode set to: {MODE}. Listening on {HOST}:{PORT}")
    threading.Thread(target=accept_loop, daemon=True).start()
    operator_cli()
    print("Server terminated.")

if __name__ == "__main__":
    main()
