"""Restricted CONNECT service and SSH tunnel; never inspect or log tunneled data."""

import argparse
import ipaddress
import json
import os
import selectors
import shlex
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


ALLOWED_TARGETS = {
    ('maimai-gm.wahlap.com', 42081),
    ('ai.sys-allnet.cn', 443),
    ('ai.sys-all.cn', 443),
}


def connect_target(line):
    method, authority, version = line.decode('ascii').split()
    target = urlsplit('//' + authority)
    if (method != 'CONNECT' or version not in ('HTTP/1.0', 'HTTP/1.1')
            or target.username is not None or target.password is not None
            or target.path or target.query or target.fragment
            or (target.hostname, target.port) not in ALLOWED_TARGETS):
        raise ValueError('target not allowed')
    return target.hostname, target.port


def copy_streams(left, right):
    left.settimeout(15)
    right.settimeout(15)
    with selectors.DefaultSelector() as selector:
        selector.register(left, selectors.EVENT_READ, right)
        selector.register(right, selectors.EVENT_READ, left)
        while selector.get_map():
            ready = selector.select(timeout=90)
            if not ready:
                return
            for key, _ in ready:
                data = key.fileobj.recv(65536)
                if data:
                    key.data.sendall(data)
                else:
                    selector.unregister(key.fileobj)
                    try:
                        key.data.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass


class RestrictedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, *, sources, upstream=None):
        self.sources = set(sources)
        self.upstream = upstream
        self.capacity = threading.BoundedSemaphore(16)
        super().__init__(address, TunnelHandler)

    def verify_request(self, request, client_address):
        return client_address[0] in self.sources

    def handle_error(self, request, client_address):
        pass


class TunnelHandler(socketserver.BaseRequestHandler):
    def handle(self):
        if not self.server.capacity.acquire(blocking=False):
            return
        try:
            self.request.settimeout(10)
            if self.server.upstream is not None:
                with socket.create_connection(self.server.upstream, timeout=10) as remote:
                    copy_streams(self.request, remote)
                return
            header = bytearray()
            while len(header) <= 8192 and not header.endswith(b'\r\n\r\n'):
                data = self.request.recv(1)
                if not data:
                    return
                header.extend(data)
            try:
                if len(header) > 8192:
                    raise ValueError
                target = connect_target(bytes(header).split(b'\r\n', 1)[0])
            except (ValueError, UnicodeError):
                self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                return
            try:
                remote = socket.create_connection(target, timeout=10)
            except OSError:
                self.request.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
                return
            with remote:
                self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                copy_streams(self.request, remote)
        except OSError:
            pass
        finally:
            self.server.capacity.release()


def private_address(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_private or address.is_unspecified:
        raise argparse.ArgumentTypeError('use a specific private IPv4 address')
    return str(address)


def relay(args):
    server = RestrictedServer((args.listen, args.port), sources=args.allow_source,
                              upstream=('127.0.0.1', args.upstream_port))
    if args.watch_stdin:
        def watch():
            sys.stdin.buffer.read()
            server.shutdown()
        threading.Thread(target=watch, daemon=True).start()
    print(json.dumps({'event': 'relay_ready'}), flush=True)
    with server:
        server.serve_forever(poll_interval=0.2)


def save_status(path, **values):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'pid': os.getpid(), **values}), encoding='utf-8')
    temporary.replace(path)


def ssh_arguments(args, local_port):
    remote = ['python3', '-u', args.remote_tool, 'relay', '--listen', args.bridge,
              '--port', str(args.bridge_port), '--upstream-port', str(args.remote_port), '--watch-stdin']
    for source in args.allow_source:
        remote.extend(['--allow-source', source])
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3',
            '-p', str(getattr(args, 'ssh_port', 22)), '-i', str(args.identity), '-R',
            f'127.0.0.1:{args.remote_port}:127.0.0.1:{local_port}',
            args.server, shlex.join(remote)]


def connect(args):
    status = args.status_file
    stop = status.with_suffix('.stop')
    stop.unlink(missing_ok=True)
    proxy = RestrictedServer(('127.0.0.1', 0), sources=['127.0.0.1'])
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    child = None
    try:
        while not stop.exists():
            save_status(status, state='connecting', connected=False)
            child = subprocess.Popen(ssh_arguments(args, proxy.server_address[1]),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            ready = threading.Event()

            def monitor(process, event):
                for line in process.stdout:
                    try:
                        if json.loads(line).get('event') == 'relay_ready':
                            event.set()
                    except (ValueError, AttributeError):
                        pass

            threading.Thread(target=monitor, args=(child, ready), daemon=True).start()
            connected = False
            while child.poll() is None and not stop.exists():
                if ready.is_set() and not connected:
                    connected = True
                    save_status(status, state='connected', connected=True, ssh_pid=child.pid,
                                proxy=f'http://{args.bridge}:{args.bridge_port}')
                    print('SSH tunnel connected; only official targets are permitted.', flush=True)
                time.sleep(0.25)
            if child.poll() is None:
                child.stdin.close()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait(timeout=5)
            if not stop.exists():
                save_status(status, state='disconnected', connected=False)
                for _ in range(20):
                    if stop.exists():
                        break
                    time.sleep(0.25)
    finally:
        if child is not None and child.poll() is None:
            child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)
        proxy.shutdown()
        proxy.server_close()
        save_status(status, state='stopped', connected=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('connect')
    start.add_argument('--server', required=True)
    start.add_argument('--ssh-port', type=int, default=22)
    start.add_argument('--identity', type=Path, default=Path.home() / '.ssh/id_ed25519')
    start.add_argument('--remote-tool', required=True)
    start.add_argument('--bridge', type=private_address, required=True)
    start.add_argument('--bridge-port', type=int, default=17890)
    start.add_argument('--remote-port', type=int, default=17891)
    start.add_argument('--allow-source', type=private_address, action='append', required=True)
    start.add_argument('--status-file', type=Path, required=True)
    forward = commands.add_parser('relay')
    forward.add_argument('--listen', type=private_address, required=True)
    forward.add_argument('--port', type=int, default=17890)
    forward.add_argument('--upstream-port', type=int, default=17891)
    forward.add_argument('--allow-source', type=private_address, action='append', required=True)
    forward.add_argument('--watch-stdin', action='store_true')
    stop = commands.add_parser('stop')
    stop.add_argument('--status-file', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'connect':
        connect(args)
    elif args.command == 'relay':
        relay(args)
    else:
        args.status_file.with_suffix('.stop').write_text('stop\n', encoding='ascii')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
