#!/usr/bin/env python3
"""
Unified Client that connects to unified_server.py.

Usage:
- Run, input server IP and your name.
- Wait for server response:
    - REJECT -> exit
    - MODE:MESSAGING -> enter text chat mode
    - MODE:VOICE -> enter voice streaming mode

In chat: type messages and press enter. Type 'leave' to exit.
In voice: type 'leave' to exit; audio will stream both ways.
"""

import socket
import threading
import time
import sys

SERVER_IP = input("Enter server IP: ").strip()
SERVER_PORT = 50007
NAME = input("Enter your name: ").strip()

CHUNK = 2048
RATE = 48000
CHANNELS = 1

client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    client_sock.connect((SERVER_IP, SERVER_PORT))
except Exception as e:
    print("Could not connect to server:", e)
    sys.exit(1)

# send handshake name
try:
    client_sock.sendall(f"NAME:{NAME}".encode())
except Exception as e:
    print("Failed to send name:", e)
    client_sock.close()
    sys.exit(1)

# wait for server response
try:
    response = client_sock.recv(4096).decode(errors='ignore').strip()
except Exception:
    print("No response from server.")
    client_sock.close()
    sys.exit(1)

if response == "REJECT":
    print("Server rejected the connection request.")
    client_sock.close()
    sys.exit(0)

if response.startswith("MODE:"):
    mode = response.split(":", 1)[1].strip()
    print(f"Connected. Server mode: {mode}")
else:
    print("Unexpected server response:", response)
    client_sock.close()
    sys.exit(1)


# ----------------- Messaging mode -----------------
def messaging_receive(sock):
    while True:
        try:
            data = sock.recv(4096)
            # if not data:
            #     print("Disconnected from server.")
            #     break
            if not data:
                continue
            else:
                text = data.decode(errors='ignore')
                print("\n" + text)
        except Exception as e:
            print("Connection lost:", e)

            break
    try:
        sock.close()
    except:
        pass
    sys.exit(0)

def messaging_send(sock):
    while True:
        try:
            msg = input("")
            if not msg:
                continue
            sock.sendall(msg.encode())
            if msg.lower() == "leave":
                print("You left the chat.")
                break
        except Exception:
            break
    try:
        sock.close()
    except:
        pass
    sys.exit(0)
   


# ----------------- Voice mode -----------------
def voice_receive(sock, stream_out):
    """
    In voice mode we may also receive short textual notifications (server notices or user lists).
    We attempt to decode incoming bytes and if it starts with markers treat as text, else treat as audio.
    """
    while True:
        try:
            data = sock.recv(CHUNK)
            if not data:
                break
            # try to decode small control messages
            if len(data) < 512:
                try:
                    s = data.decode(errors='ignore').strip()
                    if s.startswith("[USERS]"):
                        users = s[len("[USERS]"):].strip()
                        print(f"\n[Users currently in call] {users}")
                        continue
                    if s.startswith("[Server]") or s.startswith("[SERVER]") or s.startswith("[Server]:"):
                        print("\n" + s)
                        continue
                except Exception:
                    pass
            # otherwise treat as audio
            try:
                stream_out.write(data)
            except Exception:
                pass
        except Exception:
            break

def voice_send(sock, stream_in):
    while True:
        try:
            data = stream_in.read(CHUNK, exception_on_overflow=False)
            sock.sendall(data)
        except Exception:
            break

def voice_user_input(sock):
    while True:
        cmd = input("")
        if cmd.lower() == "leave":
            try:
                sock.sendall("leave".encode())
            except:
                pass
            try:
                sock.close()
            except:
                pass
            print("You left the voice call.")
            sys.exit(0)


def start_voice_mode(sock):
    # ensure pyaudio present
    try:
        import pyaudio
    except Exception:
        print("PyAudio not installed. Install with: pip install pyaudio")
        sock.close()
        sys.exit(1)

    pa = pyaudio.PyAudio()
    stream_in = pa.open(format=pyaudio.paInt16,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)
    stream_out = pa.open(format=pyaudio.paInt16,
                         channels=CHANNELS,
                         rate=RATE,
                         output=True,
                         frames_per_buffer=CHUNK)

    print("Joined voice call. Type 'leave' to exit.")

    threading.Thread(target=voice_receive, args=(sock, stream_out), daemon=True).start()
    threading.Thread(target=voice_send, args=(sock, stream_in), daemon=True).start()
    threading.Thread(target=voice_user_input, args=(sock,), daemon=True).start()

    # keep main thread alive
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        try:
            sock.sendall("leave".encode())
        except:
            pass
        sock.close()
        sys.exit(0)


# ----------------- Mode dispatch -----------------
if mode == "MESSAGING":
    threading.Thread(target=messaging_receive, args=(client_sock,), daemon=True).start()
    messaging_send(client_sock)

elif mode == "VOICE":
    start_voice_mode(client_sock)
else:
    print("Unknown mode. Disconnecting.")
    client_sock.close()
    sys.exit(1)
