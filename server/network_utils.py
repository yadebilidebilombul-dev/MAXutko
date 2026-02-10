import json
import socket
import ssl


def create_tls_context(cert_path, key_path):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.options |= ssl.OP_NO_COMPRESSION
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return ctx


def create_server_socket(host, port, backlog=100):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(backlog)
    return server


def safe_send(server, sock, data):
    if sock is None:
        return False
    try:
        if isinstance(data, str):
            data = data.encode()
        with server.send_locks_lock:
            lock = server.send_locks.get(sock)
        if lock:
            with lock:
                sock.sendall(data)
        else:
            sock.sendall(data)
        return True
    except Exception:
        return False


def safe_send_json(server, sock, payload):
    try:
        return safe_send(server, sock, json.dumps(payload) + "\n")
    except Exception:
        return False


def track_connection(server, client, ip):
    if not client or not ip:
        return 0
    with server.conn_lock:
        server.conn_ips[client] = ip
        server.conn_counts[ip] = server.conn_counts.get(ip, 0) + 1
        return server.conn_counts[ip]


def untrack_connection(server, client):
    if not client:
        return
    with server.conn_lock:
        ip = server.conn_ips.pop(client, None)
        if not ip:
            return
        current = server.conn_counts.get(ip, 0) - 1
        if current <= 0:
            server.conn_counts.pop(ip, None)
        else:
            server.conn_counts[ip] = current


def connection_limit_exceeded(server, ip, limit=20):
    if not ip:
        return False
    with server.conn_lock:
        return server.conn_counts.get(ip, 0) > limit
