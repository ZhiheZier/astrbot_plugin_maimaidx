"""Exercise the embedded view with a mock bridge; never connects to a running bot."""

import argparse
import functools
import io
import json
import threading
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
INITIAL = {
    'version': '1.5.8', 'busy': False, 'max_upload': 1024 ** 3,
    'inventory': {
        'ready': False, 'path': '/AstrBot/data/plugin_data/astrbot_plugin_maimaidx/static', 'cover_count': 1328,
        'groups': [
            {'id': 'fonts', 'name': '绘图字体', 'present': 3, 'total': 4, 'ready': False,
             'missing': ['font/FOT-NewRodin Pro EB.otf']},
            {'id': 'pictures', 'name': '基础图片', 'present': 15, 'total': 15, 'ready': True, 'missing': []},
            {'id': 'theme', 'name': '默认主题', 'present': 3, 'total': 3, 'ready': True, 'missing': []},
        ],
        'tables': [{'name': '定数表', 'count': 38, 'ready': True}, {'name': '完成表', 'count': 0, 'ready': False}],
    },
    'job': {'phase': 'idle', 'message': '暂无任务', 'current': 0, 'total': 0, 'events': [], 'cancellable': False},
    'sources': [
        {'id': 'base', 'name': '基础资源包', 'version': 'CN1.55', 'kind': '完整包'},
        {'id': 'rating', 'name': 'Rating 数字素材', 'version': 'CN1.56', 'kind': '增量包'},
        {'id': 'plates', 'name': '曲绘与牌子素材', 'version': '3.1.5', 'kind': '增量包'},
    ],
}
BRIDGE = """
window.__state = INITIAL;
window.__calls = [];
window.__context = null;
window.AstrBotPluginView = {
  ready: async () => ({isDark: false}),
  onContext: callback => { window.__context = callback; },
  apiGet: async endpoint => endpoint === 'manage/activity' ? {items: [], total: 0, page: 1, pages: 1, persistent: true} : structuredClone(window.__state),
  apiPost: async (endpoint, body) => {
    window.__calls.push({endpoint, body});
    if (window.__fail) throw new Error('测试网络错误');
    const state = window.__state;
    if (endpoint.endsWith('/download')) {
      state.busy = true;
      state.job = {phase: 'downloading', title: '基础资源包', message: '正在下载资源包',
        current: 152043520, total: 524288000, source_id: body.source_id, cancellable: true,
        events: [{at: 1794384000, message: '正在下载资源包'}]};
    } else if (endpoint.endsWith('/cancel')) {
      state.busy = false; state.job.phase = 'cancelled'; state.job.message = '任务已取消。'; state.job.cancellable = false;
    } else {
      state.busy = false; state.inventory.ready = true;
      for (const group of state.inventory.groups) {group.ready = true; group.present = group.total; group.missing = [];}
      state.job = {phase: 'completed', title: '检查资源', message: '资源检查完成。', current: 1, total: 1,
        events: [{at: 1794384000, message: '资源检查完成。'}], cancellable: false};
    }
    return structuredClone(state);
  },
  upload: async (endpoint, file) => { window.__calls.push({endpoint, fileName: file.name}); return structuredClone(window.__state); }
};
""".replace('INITIAL', json.dumps(INITIAL, ensure_ascii=False))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def check_layout(page):
    page.wait_for_function("[...document.images].every(image => image.complete && image.naturalWidth > 0)")
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'horizontal overflow'
    assert page.evaluate("""[...document.querySelectorAll('button')].filter(button => button.getClientRects().length)
        .every(button => button.scrollWidth <= button.clientWidth + 1)"""), 'button label overflow'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(QuietHandler, directory=str(ROOT / 'views/resources'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            options = {'headless': True}
            if args.browser:
                options['executable_path'] = str(args.browser)
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(viewport={'width': 1440, 'height': 1050}, device_scale_factor=1)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script(BRIDGE)
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.locator('#ready-label').filter(has_text='待补齐').wait_for()
            check_layout(page)
            page.screenshot(path=str(args.output / 'resources-desktop.png'), full_page=True)
            detail = page.locator('#inventory details').first
            detail.locator('summary').click()
            page.wait_for_timeout(5200)
            assert detail.get_attribute('open') is not None, 'poll reset details'
            page.locator('#packages button').first.click()
            page.locator('#confirm-dialog[open]').wait_for()
            page.locator('#confirm-dialog button[value=confirm]').click()
            page.locator('#job-phase').filter(has_text='下载中').wait_for()
            assert page.locator('#rescan').is_disabled()
            assert page.locator('#rebuild').is_disabled()
            page.screenshot(path=str(args.output / 'resources-progress.png'), full_page=True)
            page.locator('#cancel').click()
            page.locator('#job-phase').filter(has_text='已取消').wait_for()
            assert page.locator('#retry').is_visible()
            page.locator('#retry').click()
            page.locator('#confirm-dialog button[value=cancel]').click()
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, 'w') as bundle:
                bundle.writestr('readme.txt', 'frontend fixture only')
            page.locator('#file-input').set_input_files({'name': 'fixture.zip', 'mimeType': 'application/zip', 'buffer': payload.getvalue()})
            page.locator('#confirm-dialog button[value=confirm]').click()
            page.wait_for_function("window.__calls.some(call => call.fileName === 'fixture.zip')")
            page.locator('#rescan').click()
            page.locator('#ready-label').filter(has_text='资源就绪').wait_for()
            page.locator('#rebuild').click()
            page.wait_for_function("window.__calls.some(call => call.endpoint === 'resources/rebuild')")
            page.evaluate('window.__fail = true')
            page.locator('#rescan').click()
            page.locator('#notice').filter(has_text='测试网络错误').wait_for()
            page.evaluate('window.__context({isDark: true})')
            check_layout(page)
            page.screenshot(path=str(args.output / 'resources-dark.png'), full_page=True)
            for width in (390, 320):
                mobile = browser.new_page(viewport={'width': width, 'height': 844}, device_scale_factor=1)
                mobile.on('pageerror', lambda error: errors.append(str(error)))
                mobile.add_init_script(BRIDGE)
                mobile.goto(f'http://127.0.0.1:{server.server_port}/')
                mobile.locator('#ready-label').filter(has_text='待补齐').wait_for()
                check_layout(mobile)
                mobile.screenshot(path=str(args.output / f'resources-mobile-{width}.png'), full_page=True)
                mobile.close()
            assert not errors, errors
            browser.close()
        print('Resource view QA passed: desktop/mobile, icons, download confirmation, cancel/retry, upload, rebuild, errors and dark mode.')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    main()
