"""Read-only environment hints and a user-run SSH bootstrap script."""

import base64
import ipaddress
import platform
import re
import socket
import struct
from pathlib import Path

from .maimaidx_management_store import ManagementError


ROOT = Path(__file__).resolve().parents[1]


def network_hint():
    gateway = ''
    try:
        for line in Path('/proc/net/route').read_text(encoding='ascii').splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 4 and fields[1] == '00000000' and int(fields[3], 16) & 2:
                gateway = socket.inet_ntoa(struct.pack('<I', int(fields[2], 16)))
                break
        if gateway:
            # UDP connect chooses a local address without sending a packet.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect((gateway, 9))
                return gateway, probe.getsockname()[0]
    except (OSError, ValueError):
        pass
    return '', ''


def environment():
    system = platform.system()
    container = Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()
    bridge, source = network_hint() if system == 'Linux' and container else ('', '')
    if system == 'Linux' and not container:
        bridge = source = '127.0.0.1'
    return {'system': system, 'architecture': platform.machine(), 'container': container,
            'bridge': bridge, 'source': source,
            'warning': ('当前不是 Linux 环境；如为另一台 Linux 服务器生成脚本，请手动填写其地址。' if system != 'Linux'
                        else '容器使用宿主机网络或其他网络模式时，请核对并修正地址。' if container else '')}


def private_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
        if not address.is_private or address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError
        return str(address)
    except (ValueError, TypeError):
        raise ManagementError('请填写指定的内网或回环 IPv4 地址。') from None


def port(value):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,5}', str(value)) or not 1024 <= int(value) <= 65535:
        raise ManagementError('隧道端口须为 1024 至 65535 的整数。')
    return int(value)


def generate_script(body):
    server = body.get('server', '')
    if not isinstance(server, str) or len(server) > 253 or not re.fullmatch(
            r'[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,63}@[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', server):
        raise ManagementError('请填写 SSH 用户@服务器地址，例如 ubuntu@server.example。')
    bridge, source = private_ipv4(body.get('bridge')), private_ipv4(body.get('source'))
    bridge_port, remote_port = port(body.get('bridge_port', 17890)), port(body.get('remote_port', 17891))
    if bridge_port == remote_port:
        raise ManagementError('两个隧道端口不能相同。')
    ssh_port = body.get('ssh_port', 22)
    if isinstance(ssh_port, bool) or not re.fullmatch(r'[0-9]{1,5}', str(ssh_port)) or not 1 <= int(ssh_port) <= 65535:
        raise ManagementError('SSH 端口须为 1 至 65535 的整数。')
    payload = base64.b64encode((ROOT / 'tools/official_ssh_tunnel.py').read_bytes()).decode('ascii')
    proxy = f'http://{bridge}:{bridge_port}'
    script = f"""& {{
# Run on the Windows computer whose network can access the service.
# Requires Python 3.10+, OpenSSH, and your own SSH key. Keep this window open.
$ErrorActionPreference = 'Stop'
$Server = '{server}'
$SshPort = '{int(ssh_port)}'
$Key = Read-Host 'SSH private key path (Enter: ~/.ssh/id_ed25519)'
if ([string]::IsNullOrWhiteSpace($Key)) {{ $Key = Join-Path $HOME '.ssh/id_ed25519' }}
if (!(Test-Path -LiteralPath $Key -PathType Leaf)) {{ throw 'SSH private key not found.' }}
$Key = (Resolve-Path -LiteralPath $Key).Path
foreach ($Command in @('ssh', 'scp')) {{
  if (!(Get-Command $Command -ErrorAction SilentlyContinue)) {{ throw "Missing command: $Command" }}
}}
$Python = Read-Host 'Python 3.10+ executable (Enter: python)'
if ([string]::IsNullOrWhiteSpace($Python)) {{ $Python = 'python' }}
& $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) {{ throw 'Python 3.10+ is required.' }}
$Root = Join-Path $HOME '.maimai-ssh'
New-Item -ItemType Directory -Path $Root -Force | Out-Null
$Tool = Join-Path $Root 'official_ssh_tunnel.py'
[System.IO.File]::WriteAllBytes($Tool, [Convert]::FromBase64String('{payload}'))
$Status = Join-Path $Root 'status.json'
# Check the server fingerprint before accepting it on the first connection.
$RemoteSetup = 'umask 077; mkdir -p .cache/maimai-ssh; python3 -c ''import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'''
& ssh -T -p $SshPort -o StrictHostKeyChecking=ask -o ConnectTimeout=10 -i $Key $Server $RemoteSetup
if ($LASTEXITCODE -ne 0) {{ throw 'SSH login or remote Python check failed.' }}
& scp -P $SshPort -o StrictHostKeyChecking=yes -o BatchMode=yes -i $Key $Tool "${{Server}}:.cache/maimai-ssh/official_ssh_tunnel.py"
if ($LASTEXITCODE -ne 0) {{ throw 'Failed to install remote tunnel helper.' }}
Write-Host 'Plugin official_http_proxy: {proxy}'
Write-Host 'Keep this window open. Press Ctrl+C to stop. No plugin settings are changed automatically.'
& $Python $Tool connect --server $Server --ssh-port $SshPort --identity $Key --remote-tool '.cache/maimai-ssh/official_ssh_tunnel.py' --bridge '{bridge}' --bridge-port {bridge_port} --remote-port {remote_port} --allow-source '{source}' --status-file $Status
}}
"""
    return {'script': script, 'proxy': proxy, 'message': '连接脚本已生成，尚未执行或修改配置。'}
