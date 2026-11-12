"""End-to-end management QA against real handlers and isolated synthetic test stores."""

import argparse
import asyncio
import copy
import functools
import json
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock

from playwright.sync_api import sync_playwright

from check_resource_view import INITIAL, ROOT, QuietHandler, check_layout
from data.plugins.astrbot_plugin_maimaidx.tests.test_management_extra import ExtraManagementTest
from data.plugins.astrbot_plugin_maimaidx.tests.test_nearcade import shop


BRIDGE = """
window.__context = null;
window.AstrBotPluginView = {
 ready: async () => ({isDark: false}), onContext: callback => {window.__context = callback;},
 apiGet: async (endpoint, params = {}) => {
   const response = await fetch('/qa/' + endpoint + '?' + new URLSearchParams(params));
   const data = await response.json(); if (!response.ok) throw new Error(data.message); return data;
 },
 apiPost: async (endpoint, body) => {
   const response = await fetch('/qa/' + endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
   const data = await response.json(); if (!response.ok) throw new Error(data.message); return data;
 }
};
"""


def check_activity_timestamps(page):
    times = page.locator('#activity-events time')
    assert times.count() > 0
    for item in times.all():
        assert re.fullmatch(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}', item.inner_text()), 'missing slash-separated activity date'
        assert item.get_attribute('datetime').endswith('Z'), 'missing machine-readable timestamp'
        assert item.evaluate('item => item.scrollWidth <= item.clientWidth + 1'), 'timestamp clipped'
    assert page.locator('.activity-line').evaluate_all('items => items.every(item => item.scrollWidth <= item.clientWidth + 1)'), 'activity header overflow'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    loop = asyncio.new_event_loop()
    worker = threading.Thread(target=loop.run_forever, daemon=True)
    worker.start()
    fixture = ExtraManagementTest('test_unknown_modules_and_incorrect_confirmation_do_not_change_data')

    def run(coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=30)

    run(fixture.asyncSetUp())
    resource_state = copy.deepcopy(INITIAL)
    from data.plugins.astrbot_plugin_maimaidx import __version__
    resource_state['version'] = __version__

    async def seed():
        from data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_management_store import write_json
        from data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_activity import ActivityLog
        fixture.plugin.activity = ActivityLog(fixture.root / 'activity.json')
        await fixture.plugin.activity.load()
        for index in range(34):
            fixture.plugin.activity.record('web', 'manage_players', '维护玩家设置', 'handled')
        fixture.plugin.activity.record('chat', 'best50', 'B50 查询', 'responded', actor='200', group='456', bot='987', duration_ms=123)
        fixture.plugin.activity.record('resource', 'resource_task', '资源任务完成', 'completed')
        fixture.plugin.nearcade._request = AsyncMock(return_value={'shop': shop()})
        await fixture.plugin.song_ranking_store.set_member('987:200', '200', '示例玩家', joined=True)
        for qq in range(3000, 3094):
            await fixture.plugin.song_ranking_store.set_member('987:200', str(qq), f'分页玩家 {qq}', joined=True)
        identity, _ = await fixture.make_snapshot()
        snapshot = await fixture.plugin.score_sync.store.load(identity)
        snapshot.scores = [snapshot.scores[0].model_copy(update={'song_id': song_id}) for song_id in range(1, 96)]
        await fixture.plugin.score_sync.store.save(identity, snapshot)
        fixture.plugin.enabled_groups.update(str(group) for group in range(1000, 1094))
        await fixture.plugin.score_sync.bindings.bind('200', 12345)
        fixture.plugin.update_notifier.changelog.write_text(
            '## v1.5.8\n### 新功能\n- 当前版本完整更新\n## v1.5.7\n### 修复\n- 上一版本修复\n'
            '## v1.5.6\n### 优化\n- 旧版本优化\n', encoding='utf-8')
        await fixture.plugin.score_sync.limits.reserve(('qq-test', '987', '200'), 'fetch')
        write_json(fixture.extra.cache_paths()['merged_music'], [])
        write_json(fixture.extra.cache_paths()['music'], [])
    run(seed())

    async def invoke(endpoint, params, post):
        if endpoint == 'resources/status':
            return resource_state
        if endpoint == 'manage/status':
            return fixture.manager.status()
        if endpoint == 'manage/extra':
            return await (fixture.extra.change(params) if post else fixture.extra.listing(params))
        if endpoint == 'manage/detail':
            return await fixture.extra.detail(params)
        if endpoint == 'manage/activity':
            return fixture.plugin.activity.listing(params)
        if endpoint == 'manage/groups':
            return fixture.manager.groups(params)
        if endpoint == 'manage/group':
            return fixture.manager.change_group(params)
        if endpoint == 'manage/arcades':
            return await fixture.manager.arcades(params)
        if endpoint == 'manage/arcade':
            return await fixture.manager.change_arcade(params)
        if endpoint == 'manage/songs':
            return fixture.manager.songs(params)
        raise ValueError('unsupported test route')

    class Handler(QuietHandler):
        def rpc(self, post=False):
            parsed = urlsplit(self.path)
            params = (json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)))) if post
                      else {key: values[-1] for key, values in parse_qs(parsed.query).items()})
            try:
                data = run(invoke(parsed.path.removeprefix('/qa/'), params, post))
                status = 200
            except Exception as exc:
                data, status = {'message': str(exc)}, 409
            payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(payload)

        def do_GET(self):
            if self.path.startswith('/qa/'):
                self.rpc()
            else:
                super().do_GET()

        def do_POST(self):
            self.rpc(True)

    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT / 'views/resources')))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with sync_playwright() as playwright:
            options = {'headless': True}
            if args.browser:
                options['executable_path'] = str(args.browser)
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error))); page.add_init_script(BRIDGE)
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.locator('#ready-label').filter(has_text='待补齐').wait_for()
            expanded_width = page.locator('.console-content').bounding_box()['width']
            page.locator('#sidebar-toggle').click()
            page.locator('#task-sidebar').wait_for(state='hidden')
            assert page.locator('.console-content').bounding_box()['width'] > expanded_width + 250
            assert page.locator('#sidebar-toggle').get_attribute('aria-expanded') == 'false'
            page.locator('#tab-groups').click()
            assert not page.locator('#task-sidebar').is_visible()
            page.locator('#tab-resources').click()
            page.screenshot(path=str(args.output / 'sidebar-collapsed.png'))
            page.locator('#sidebar-toggle').click()
            page.locator('#task-sidebar').wait_for(state='visible')
            page.locator('#activity-count').filter(has_text='36 条').wait_for()
            page.locator('#activity-scopes button[data-scope=chat]').click()
            page.locator('#activity-count').filter(has_text='1 条').wait_for()
            page.locator('#activity-events').get_by_text('B50 查询', exact=True).wait_for()
            check_activity_timestamps(page)
            page.locator('#activity-scopes button[data-scope=all]').click()
            page.locator('#activity-next').click()
            page.locator('#activity-page').filter(has_text='2 / 2').wait_for()
            page.locator('#activity-prev').click()
            page.locator('#activity-page').filter(has_text='1 / 2').wait_for()
            page.screenshot(path=str(args.output / 'resources-header.png'), full_page=True)
            page.locator('#tab-groups').click()
            assert not page.locator('#resource-path').is_visible()
            assert not page.locator('#rescan').is_visible()
            page.locator('#groups-pages input').fill('3')
            page.locator('#groups-pages input').press('Enter')
            page.locator('#groups-pages .page-summary').filter(has_text='3 / 4 页').wait_for()
            page.locator('#groups-pages').get_by_role('button', name='末页', exact=True).click()
            page.locator('#groups-pages .page-summary').filter(has_text='4 / 4 页').wait_for()
            assert page.locator('#groups-pages').get_by_role('button', name='下一页', exact=True).is_disabled()
            page.locator('#groups-pages input').fill('99')
            page.locator('#groups-pages input').press('Enter')
            assert page.locator('#groups-pages .page-summary').inner_text().endswith('4 / 4 页')
            page.locator('#groups-pages').get_by_role('button', name='首页', exact=True).click()
            page.locator('#groups-pages .page-summary').filter(has_text='1 / 4 页').wait_for()
            page.locator('#groups-pages-top').wait_for(state='visible')
            page.screenshot(path=str(args.output / 'groups-pagination.png'), full_page=True)
            page.locator('#groups-q').fill('200'); page.locator('#groups-search button[type=submit]').click()
            page.locator('#groups-pages .page-summary').filter(has_text='1 / 1 页').wait_for()
            page.locator('#groups-pages-top').wait_for(state='hidden')
            assert page.locator('#groups-pages').is_visible()
            page.screenshot(path=str(args.output / 'short-list-single-pagination.png'))
            page.set_viewport_size({'width': 1440, 'height': 580})
            page.locator('#groups-pages-top').wait_for(state='visible')
            page.wait_for_timeout(300)
            assert page.locator('#groups-pages-top').is_visible(), 'pager oscillates near min-height threshold'
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.locator('#groups-pages-top').wait_for(state='hidden')
            page.locator('#tab-arcades').click()
            page.get_by_role('combobox', name='数据源', exact=True).click()
            page.screenshot(path=str(args.output / 'custom-select.png'))
            page.get_by_role('listbox').press('End'); page.get_by_role('listbox').press('Enter')
            page.wait_for_function("document.querySelector('#arcades-source').value === 'nearcade'")
            page.locator('#arcades-body button').first.click()
            box = page.locator('#arcade-group').bounding_box(); button = page.locator('#arcade-subscribe').bounding_box()
            assert abs((box['y'] + box['height']) - (button['y'] + button['height'])) <= 2, 'subscription button misaligned'
            page.screenshot(path=str(args.output / 'subscription-alignment.png'))
            page.locator('#arcade-dialog').get_by_role('button', name='关闭机厅设置').click()
            page.locator('#tab-more').click()
            page.locator('#extra-add').click()
            page.locator('#mapping-local').fill('123'); page.locator('#mapping-alias').fill('mml'); page.locator('#mapping-shop').fill('456')
            page.get_by_role('button', name='查询店铺机台', exact=True).click()
            page.get_by_role('button', name='选择机台', exact=True).click()
            page.get_by_role('button', name='保存映射', exact=True).click()
            page.locator('#extra-dialog').wait_for(state='hidden')
            page.locator('#extra-body').get_by_text('123[mml]=456:456000', exact=True).wait_for()
            page.screenshot(path=str(args.output / 'extra-mappings.png'))
            page.locator('#tab-players').click()
            assert not page.locator('#extra-tabs').is_visible()
            assert page.locator('#tab-players').get_attribute('aria-selected') == 'true'
            page.locator('#extra-body').get_by_text('游戏 userId：12345').wait_for()
            assert page.locator('.player-authorization').evaluate("item => {const [a,b] = [...item.children].map(child => getComputedStyle(child)); return a.fontSize === b.fontSize && a.color === b.color && a.fontWeight === b.fontWeight;}")
            page.locator('#extra-body').get_by_role('button', name='编辑玩家设置').click()
            page.get_by_role('combobox', name='绘图主题', exact=True).click()
            page.get_by_role('option', name='circle', exact=True).click()
            page.get_by_role('button', name='保存设置', exact=True).click()
            page.locator('#extra-error').filter(has_text='玩家设置已保存').wait_for()
            from data.plugins.astrbot_plugin_maimaidx.libraries.maimaidx_user import userstore
            assert userstore.get('200').theme.value == 'circle'
            page.screenshot(path=str(args.output / 'extra-player-editor.png'))
            boxes = page.locator('.player-actions button').evaluate_all('items => items.map(item => {const b = item.getBoundingClientRect(); return {x:b.x,y:b.y,right:b.right};})')
            assert len(boxes) == 5 and boxes[0]['y'] == boxes[1]['y'] == boxes[2]['y']
            assert boxes[3]['y'] == boxes[4]['y'] > boxes[2]['y']
            assert abs(boxes[2]['right'] - boxes[4]['right']) <= 1
            page.get_by_role('button', name='解绑游戏账号', exact=True).click()
            page.locator('#extra-confirm-input').fill('解绑游戏账号')
            page.locator('#extra-confirm button[value=confirm]').click()
            page.locator('#extra-error').filter(has_text='游戏账号已解绑').wait_for()
            assert run(fixture.plugin.score_sync.bindings.records()) == {}
            assert userstore.get('200').access_token == 'never-expose-access'
            assert page.get_by_role('button', name='解绑游戏账号', exact=True).is_disabled()
            page.locator('#extra-dialog-close').click()
            page.locator('#tab-more').click()
            for key in ('rankings', 'pc', 'cache', 'limits', 'notices'):
                page.locator(f'#extra-tab-{key}').click()
                page.locator('#extra-title').filter(has_text={'rankings': '群排行榜', 'pc': 'PC 数据', 'cache': '缓存维护', 'limits': '同步额度', 'notices': '更新通知'}[key]).wait_for()
                check_layout(page)
                page.screenshot(path=str(args.output / f'extra-{key}.png'))
                if key == 'rankings':
                    page.locator('#extra-pages input').fill('3')
                    page.locator('#extra-pages').get_by_role('button', name='跳转', exact=True).click()
                    page.locator('#extra-pages .page-summary').filter(has_text='3 / 4 页').wait_for()
                    page.screenshot(path=str(args.output / 'rankings-pagination.png'), full_page=True)
                if key == 'pc':
                    page.get_by_role('button', name='查看快照成绩', exact=True).click()
                    page.locator('.extra-detail-table tbody tr').first.wait_for()
                    page.locator('.extra-detail-pager input').first.fill('4')
                    page.locator('.extra-detail-pager input').first.press('Enter')
                    page.locator('.extra-detail-pager .page-summary').last.filter(has_text='4 / 4 页').wait_for()
                    page.locator('.extra-detail-pager.pagination-top').wait_for(state='hidden')
                    assert page.locator('.extra-detail-table tbody tr').count() == 5
                    page.screenshot(path=str(args.output / 'extra-pc-detail.png'))
                    page.locator('#extra-dialog-close').click()
                if key == 'limits':
                    page.get_by_role('button', name='重置额度', exact=True).click()
                    page.locator('#extra-confirm-input').fill('重置额度')
                    page.locator('#extra-confirm button[value=confirm]').click()
                    page.locator('#extra-body').get_by_text('暂无匹配记录').wait_for()
                if key == 'cache':
                    assert not page.locator('#extra-search').is_visible()
                    assert page.locator('#extra-refresh').is_visible()
                if key == 'notices':
                    assert page.locator('#extra-search').is_visible()
                    assert page.locator('#tab-notices').count() == 0
                    page.wait_for_function("document.querySelector('#extra-body').children.length === 3")
                    page.locator('#extra-q').fill('修复')
                    page.locator('#extra-search button[type=submit]').click()
                    page.wait_for_function("document.querySelector('#extra-body').children.length === 1")
                    page.get_by_role('button', name='查看 v1.5.7 更新', exact=True).click()
                    notes = page.locator('#extra-editor').inner_text()
                    assert '上一版本修复' in notes
                    assert all(value not in notes for value in ('当前版本完整更新', '/maihelp', 'contact@', '完整更新日志：'))
                    page.screenshot(path=str(args.output / 'notice-single-version.png'))
                    page.locator('#extra-dialog-close').click()
                    page.get_by_role('button', name='预览完整通知', exact=True).click()
                    full = page.locator('#extra-editor').inner_text()
                    assert all(value in full for value in ('舞萌DX插件当前版本：v1.5.8', '当前版本完整更新', '/maihelp', 'contact@', '完整更新日志：'))
                    assert '上一版本修复' not in full
                    page.screenshot(path=str(args.output / 'notice-full-preview.png'))
                    page.locator('#extra-dialog-close').click()
                    fixture.client.send_group_forward_msg.assert_not_awaited()
                    page.locator('#notice-group').fill('200')
                    page.get_by_role('button', name='发送到指定群', exact=True).click()
                    page.locator('#extra-confirm-input').fill('200')
                    page.locator('#extra-confirm button[value=confirm]').click()
                    page.locator('#manage-notice').filter(has_text='已向指定群发送').wait_for()
                    fixture.client.send_group_forward_msg.assert_awaited_once()
                    page.locator('#extra-q').fill('')
                    changelog = fixture.plugin.update_notifier.changelog
                    full_changelog = changelog.read_text(encoding='utf-8')
                    changelog.write_text('## v1.5.8\n### 新功能\n- 当前版本完整更新\n', encoding='utf-8')
                    page.locator('#extra-refresh').click()
                    page.locator('#extra-body').get_by_text('v1.5.8', exact=True).wait_for()
                    page.locator('#extra-pages').wait_for(state='hidden')
                    assert not page.locator('#extra-pages-top').is_visible()
                    changelog.write_text(full_changelog, encoding='utf-8')
                    page.locator('#extra-refresh').click()
                    page.wait_for_function("document.querySelector('#extra-body').children.length === 3")
                    page.screenshot(path=str(args.output / 'notice-version-list.png'))
            # Exercise the real execCommand fallback, not just a mocked success.
            page.evaluate("""() => {
              window.__copied = '';
              Object.defineProperty(navigator, 'clipboard', {configurable: true, value: undefined});
              document.addEventListener('copy', () => {
                const area = document.activeElement;
                window.__copied = area.value?.slice(area.selectionStart, area.selectionEnd) || '';
              });
            }""")
            page.locator('#tab-resources').click()
            page.locator('#copy-path').click()
            page.wait_for_function('window.__copied === ' + json.dumps(resource_state['inventory']['path']))
            page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async () => {throw new Error('denied');}}})")
            page.locator('#copy-path').click()
            page.locator('#notice').filter(has_text='资源目录已复制').wait_for()
            assert page.evaluate("document.activeElement.id === 'copy-path'")
            page.locator('#notice').wait_for(state='hidden', timeout=5000)
            page.locator('#tab-more').click()
            page.locator('#extra-tab-tunnel').click()
            page.locator('#tunnel-server').fill('ubuntu@server.example')
            page.locator('#tunnel-bridge').fill('172.18.0.1')
            page.locator('#tunnel-source').fill('172.18.0.2')
            page.locator('#tunnel-ssh-port').fill('2222')
            page.get_by_role('button', name='生成连接脚本', exact=True).click()
            page.locator('#tunnel-script').wait_for()
            generation_notice = page.locator('#tunnel-generation-notice')
            generation_notice.filter(has_text='连接脚本已生成').wait_for()
            generate_box = page.get_by_role('button', name='生成连接脚本', exact=True).bounding_box()
            notice_box = generation_notice.bounding_box()
            assert notice_box['y'] >= generate_box['y'] + generate_box['height'], 'generation notice is not below its button'
            assert not page.locator('#manage-notice').is_visible(), 'generation notice leaked into page banner'
            page.screenshot(path=str(args.output / 'ssh-tunnel-inline-notice.png'), full_page=True)
            generation_notice.wait_for(state='hidden', timeout=5000)
            script = page.locator('#tunnel-script').input_value()
            assert "--bridge '172.18.0.1'" in script and "$SshPort = '2222'" in script
            (args.output / 'generated-tunnel.ps1').write_text(script, encoding='utf-8')
            page.get_by_role('button', name='复制连接脚本', exact=True).click()
            assert page.evaluate('window.__copied') == script
            page.get_by_role('button', name='复制代理地址', exact=True).click()
            assert page.evaluate('window.__copied') == 'http://172.18.0.1:17890'
            page.evaluate('window.__execCommand = document.execCommand; document.execCommand = () => false')
            page.get_by_role('button', name='复制连接脚本', exact=True).click()
            page.locator('#manage-notice').filter(has_text='浏览器禁止复制').wait_for()
            assert page.evaluate("document.activeElement.id === 'tunnel-script'")
            page.wait_for_timeout(3200)
            assert page.locator('#manage-notice').is_visible(), 'old success timer hid a new error'
            page.evaluate('() => {document.execCommand = window.__execCommand;}')
            page.get_by_role('button', name='复制代理地址', exact=True).click()
            page.wait_for_timeout(1800)
            page.get_by_role('button', name='复制代理地址', exact=True).click()
            page.wait_for_timeout(1500)
            assert page.locator('#manage-notice').is_visible(), 'repeat success did not reset the timer'
            page.locator('#manage-notice').wait_for(state='hidden', timeout=5000)
            assert not page.locator('#extra-search').is_visible()
            assert not page.locator('#extra-table-wrap').is_visible()
            assert not page.locator('#extra-pages').is_visible()
            check_layout(page)
            page.screenshot(path=str(args.output / 'ssh-tunnel-desktop.png'), full_page=True)
            page.locator('#tunnel-source').fill('172.18.0.3')
            assert not page.locator('#tunnel-script').is_visible(), 'stale script still copyable'
            page.locator('#tunnel-server').fill('invalid-server')
            page.get_by_role('button', name='生成连接脚本', exact=True).click()
            generation_notice.filter(has_text='请填写 SSH').wait_for()
            page.wait_for_timeout(3200)
            assert generation_notice.is_visible(), 'generation error disappeared'
            assert not page.locator('#manage-notice').is_visible(), 'generation error leaked into page banner'
            page.locator('#tunnel-server').fill('ubuntu@server.example')
            assert not generation_notice.is_visible(), 'editing did not clear obsolete generation error'
            for width in (390, 320):
                page.set_viewport_size({'width': width, 'height': 844})
                page.locator('#sidebar-toggle').click()
                assert not page.locator('#task-sidebar').is_visible()
                check_layout(page)
                page.locator('#sidebar-toggle').click()
                assert page.locator('#task-sidebar').is_visible()
                check_activity_timestamps(page)
                for key in ('mappings', 'players', 'rankings', 'pc', 'notices', 'cache', 'limits', 'tunnel'):
                    if key == 'players':
                        page.locator(f'#tab-{key}').click()
                    else:
                        page.locator('#tab-more').click()
                        page.locator(f'#extra-tab-{key}').click()
                    page.wait_for_timeout(150); check_layout(page)
                    if key == 'tunnel':
                        page.locator('#tunnel-server').fill('ubuntu@server.example')
                        page.locator('#tunnel-bridge').fill('172.18.0.1')
                        page.locator('#tunnel-source').fill('172.18.0.2')
                        page.get_by_role('button', name='生成连接脚本', exact=True).click()
                        page.locator('#tunnel-script').wait_for()
                        notice_box = page.locator('#tunnel-generation-notice').bounding_box()
                        generate_box = page.get_by_role('button', name='生成连接脚本', exact=True).bounding_box()
                        assert notice_box['y'] >= generate_box['y'] + generate_box['height']
                        check_layout(page)
                    page.screenshot(path=str(args.output / f'extra-{key}-{width}.png'), full_page=True)
                    if key == 'players':
                        page.locator('#extra-body').get_by_role('button', name='编辑玩家设置').click()
                        check_layout(page)
                        boxes = page.locator('.player-actions button').evaluate_all('items => items.map(item => {const b = item.getBoundingClientRect(); return {y:b.y,right:b.right};})')
                        page.screenshot(path=str(args.output / f'player-actions-{width}.png'))
                        assert boxes[0]['y'] < boxes[1]['y'] == boxes[2]['y'] < boxes[3]['y'] == boxes[4]['y']
                        page.locator('#extra-dialog-close').click()
                    if key == 'rankings':
                        page.locator('#extra-pages-top').scroll_into_view_if_needed()
                        page.screenshot(path=str(args.output / f'pagination-top-{width}.png'))
                page.locator('#tab-arcades').click(); page.locator('#arcades-body button').first.click()
                box = page.locator('#arcade-group').bounding_box(); button = page.locator('#arcade-subscribe').bounding_box()
                assert abs((box['y'] + box['height']) - (button['y'] + button['height'])) <= 2
                page.screenshot(path=str(args.output / f'subscription-alignment-{width}.png'))
                page.locator('#arcade-dialog').get_by_role('button', name='关闭机厅设置').click()
                page.locator('#tab-more').click()
            page.set_viewport_size({'width': 1440, 'height': 1000})
            resource_state.update(busy=True, job={'phase': 'rebuilding', 'title': '生成定数表和完成表', 'message': '正在等待绘图结束并生成表格', 'events': []})
            page.locator('#tab-resources').click()
            page.locator('#job-phase').filter(has_text='生成中').wait_for()
            task = page.locator('.task-summary').bounding_box()
            progress = page.locator('#progress').bounding_box()
            message = page.locator('#job-message').bounding_box()
            assert progress['y'] - message['y'] - message['height'] < 25, 'task progress separated from message'
            assert task['height'] < 400, 'task summary stretched to content height'
            page.screenshot(path=str(args.output / 'task-sidebar.png'), full_page=True)
            assert not errors, errors
            browser.close()
        print('Extended management E2E passed: existing management flows, HTTP clipboard fallback, SSH script generation/copy and desktop/mobile layouts.')
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
        run(fixture.plugin.activity.shutdown()); run(fixture.asyncTearDown()); fixture.doCleanups()
        loop.call_soon_threadsafe(loop.stop); worker.join(timeout=3); loop.close()


if __name__ == '__main__':
    main()
