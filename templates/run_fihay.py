import socket
import threading
import time
import webbrowser
import uvicorn


def find_free_port(start=8000, end=8020):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found between 8000 and 8020.")


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


if __name__ == "__main__":
    port = find_free_port()
    local_url = f"http://127.0.0.1:{port}"
    phone_url = f"http://{local_ip()}:{port}"
    print("\nFiHay FRMS v2.0 local test server is starting.")
    print(f"This computer: {local_url}")
    print(f"Phone/tablet on the same Wi-Fi or hotspot: {phone_url}")
    print("This local mode is for development/testing. Hosted farmers will use the deployed HTTPS URL.")
    print("Keep this window open while using the system. Press Ctrl+C to stop it.\n")
    threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(local_url)), daemon=True).start()
    uvicorn.run("app:app", host="0.0.0.0", port=port)
