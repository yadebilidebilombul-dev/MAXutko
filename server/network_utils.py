import json
import os
import socket
import ssl
import tempfile

MAX_LINE_BYTES = 512 * 1024  # 512 KB максимум на один JSON-пакет


class LineBufferExceededError(ValueError):
    pass


def recv_line(sock, byte_buffer, max_bytes=MAX_LINE_BYTES):
    """
    Безопасно читает одну строку (до b'\\n') из сокета, работая с байтами.
    Возвращает кортеж (line_str, remaining_byte_buffer).
    Если соединение закрыто, возвращает (None, byte_buffer).
    """
    while b"\n" not in byte_buffer:
        if len(byte_buffer) > max_bytes:
            raise LineBufferExceededError("Incoming line exceeded maximum allowed size")
        chunk = sock.recv(4096)
        if not chunk:
            return None, byte_buffer
        byte_buffer += chunk
        if len(byte_buffer) > max_bytes and b"\n" not in byte_buffer:
            raise LineBufferExceededError("Incoming line exceeded maximum allowed size")

    raw_line, byte_buffer = byte_buffer.split(b"\n", 1)
    if len(raw_line) > max_bytes:
        raise LineBufferExceededError("Incoming line exceeded maximum allowed size")
    return raw_line.decode("utf-8", errors="replace"), byte_buffer


def atomic_write_json(path, data):
    """Атомарно записывает JSON через временный файл, исключая порчу данных при сбое."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise


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
            data = data.encode("utf-8")
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
        return safe_send(server, sock, json.dumps(payload, ensure_ascii=False) + "\n")
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