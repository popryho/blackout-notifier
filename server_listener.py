import socket
import sys

# Listen on all interfaces, Port 57321
HOST = '0.0.0.0'
PORT = 57321

def run_server():
    # AF_INET = IPv4, SOCK_STREAM = TCP
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Allow immediate reuse of the port after restart
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, PORT))
            s.listen(1)
            # print statements here will go to the systemd journal/log
            print(f"Listening on {HOST}:{PORT}...", flush=True)
        except Exception as e:
            print(f"Failed to bind: {e}", file=sys.stderr)
            return

        while True:
            try:
                conn, addr = s.accept()
                with conn:
                    # Immediately close connection (handshake complete)
                    pass
            except Exception as e:
                print(f"Error accepting connection: {e}", file=sys.stderr)

if __name__ == "__main__":
    run_server()
