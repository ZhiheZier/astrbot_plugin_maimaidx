"""Credential-safe HTTPS diagnostics; no native runtime installation is needed."""

import argparse
import asyncio
import getpass
import importlib
import importlib.util
import sys
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]


def default_root():
    if PLUGIN.parent.name == 'plugins':
        return PLUGIN.parent.parent / 'plugin_data/astrbot_plugin_maimaidx'
    return Path.cwd() / 'official-sync-data'


def load_module(name):
    if PLUGIN.parent.name == 'plugins':
        astrbot_root = PLUGIN.parents[2]
        if (astrbot_root / 'astrbot').is_dir() and str(astrbot_root) not in sys.path:
            sys.path.insert(0, str(astrbot_root))
    package = '_maimaidx_https_check'
    if package not in sys.modules:
        spec = importlib.util.spec_from_file_location(package, PLUGIN / '__init__.py',
                                                     submodule_search_locations=[str(PLUGIN)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f'{package}.libraries.{name}')


async def diagnose(args):
    official = load_module('maimaidx_official')
    client = official.OfficialClient(args.data_dir, {
        'official_sync_enabled': True, 'official_client_id': args.client_id,
        'official_place_id': args.place_id,
    })
    tls = await client.check_available()
    print('HTTPS dependencies and certificate verification: OK')
    print('No DLL, Wine, architecture-specific bridge or runtime download required.')
    print(f'Protocol identifiers: chipId={client.keychip_id}, clientId={client.client_id}, placeId={client.place_id}')
    if args.network_check or args.fetch:
        servers = await client.login_server_order()
        for index, host in enumerate(official.LOGIN_HOSTS):
            print(f'Login server {index + 1} {host}: DNS {"OK" if index in servers else "FAILED"}')
        async with client.http_session(tls, timeout=15) as http:
            reachable = []
            for index in servers:
                try:
                    await client.probe_login(http, index)
                    reachable.append(index)
                    print(f'Login server {index + 1}: verified HTTPS alive')
                except Exception as exc:
                    print(f'Login server {index + 1}: check failed ({type(exc).__name__}); details withheld.')
            if not reachable:
                raise official.OfficialError('没有通过证书校验及探活的 HTTPS 登录线路，未提示输入 SGID。')
            settings = await client._post(http, official.TITLE_URL, 'GetGameSettingApi', 0,
                                          {'placeId': client.place_id, 'clientId': client.client_id})
        if not isinstance(settings.get('gameSetting'), dict):
            raise official.OfficialError('官方连通检查未返回有效设置。')
        print('Official settings endpoint: verified HTTPS OK (no player login performed)')
    if args.fetch:
        if not sys.stdin.isatty():
            raise ValueError('--fetch requires an interactive terminal; never pass SGID as an argument')
        credential = getpass.getpass('Fresh SGID (hidden, quit the cabinet first): ')
        try:
            result = await client.fetch(credential)
        finally:
            credential = ''
        snapshot = load_module('maimaidx_sync').snapshot_from_official(result)
        counts = [score.play_count for score in snapshot.scores if score.play_count is not None]
        print(f'Official fetch: {len(snapshot.scores)} charts; {len(counts)} charts with PC; total PC={sum(counts)}')
        print('No prober uploads performed. No SGID, session token or player ID saved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=default_root())
    parser.add_argument('--install', action='store_true', help='legacy option; no files are downloaded')
    parser.add_argument('--probe', action='store_true', help='check Python dependencies and TLS setup, without SGID')
    parser.add_argument('--fetch', action='store_true', help='verified HTTPS preflight and hidden SGID prompt; fetch only')
    parser.add_argument('--network-check', action='store_true', help='verify HTTPS login health and official settings')
    parser.add_argument('--client-id', default='')
    parser.add_argument('--place-id', type=int, default=3496)
    args = parser.parse_args()
    try:
        if args.install:
            print('Native runtime installation is no longer needed; no files downloaded or changed.')
        asyncio.run(diagnose(args))
    except Exception as exc:
        if isinstance(exc, ModuleNotFoundError):
            print(f'Missing Python dependency: {exc.name}; install requirements.txt in the AstrBot environment.', file=sys.stderr)
        elif any(cls.__name__ == 'OfficialError' for cls in type(exc).__mro__):
            print(str(exc), file=sys.stderr)
        else:
            print(f'HTTPS check failed ({type(exc).__name__}); no credentials logged.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
