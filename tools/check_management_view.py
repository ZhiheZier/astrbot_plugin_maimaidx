"""Browser QA of management tabs with synthetic data and a mock dashboard bridge."""

import argparse
import functools
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from check_resource_view import BRIDGE, ROOT, QuietHandler, check_layout


MANAGEMENT_BRIDGE = r"""
const originalGet = window.AstrBotPluginView.apiGet;
const originalPost = window.AstrBotPluginView.apiPost;
window.__mg = {
 modes: {maimai_whitelist: true, alias_whitelist: false, alias_global: true, arcade_source: 'local'},
 groups: Array.from({length: 31}, (_, n) => ({id: String(200 + n), maimai: n !== 2, disabled: n === 2,
   arcade: n % 2 === 0, guess: false, alias: true, alias_disabled: false, revision: 'v1',
   effective: {maimai: n !== 2, arcade: n % 2 === 0 && n !== 2, guess: false, alias: true}})),
 arcades: [{id: '123', name: '示例机厅（天街店）', location: '金沙大道560号 三楼', province: '浙江', mall: '天街',
   num: 2, person: 5, status: '营业', alias: ['天街'], group: [200], group_aliases: {'200': ['附近店']}, game_machine_counts: {}, source: 'local', revision: 'v1'},
   {id: '456', name: '示例云端店', location: '云端店铺地址', province: '', mall: '', num: 3,
   person: 0, status: '营业', alias: [], group: [], group_aliases: {}, game_machine_counts: {}, source: 'nearcade', revision: 'v1'}],
 songs: [{id: '11667', name: 'にっこり^^調査隊のテーマ', type: 'DX', local: ['笑脸'], other: ['调查队', 'ニッコリ'], revision: 'v1'},
   {id: '11860', name: 'MYTH Re：LEASE', type: 'DX', local: [], other: ['神话重释'], revision: 'v1'}]
};
window.AstrBotPluginView.apiGet = async (endpoint, params = {}) => {
 if (!endpoint.startsWith('manage/')) return originalGet(endpoint, params);
 window.__calls.push({endpoint, params});
 if (endpoint === 'manage/status') return structuredClone(window.__mg.modes);
 const key = endpoint.split('/')[1];
 let items = window.__mg[key] || [];
 if (key === 'arcades') items = items.filter(item => item.source === params.source).map(item =>
   ({...item, subscribed: item.group.includes(Number(params.group))}));
 if (params.q) items = items.filter(item => [item.id, item.name || '', ...(item.local || []), ...(item.alias || [])].some(value => value.includes(params.q)));
 if (params.subscribed === '1') items = items.filter(item => item.subscribed);
 if (params.local_only === '1') items = items.filter(item => item.local.length);
 const page = Number(params.page || 1);
 return structuredClone({items: items.slice((page - 1) * 30, page * 30), page, pages: Math.max(1, Math.ceil(items.length / 30)),
   total: items.length, source: params.source, modes: window.__mg.modes});
};
window.AstrBotPluginView.apiPost = async (endpoint, body) => {
 if (!endpoint.startsWith('manage/')) return originalPost(endpoint, body);
 window.__calls.push({endpoint, body});
 if (window.__mgFail) throw new Error('数据已变化，请刷新列表后重新操作。');
 if (endpoint === 'manage/group') {
   const item = window.__mg.groups.find(item => item.id === body.id);
   item[body.field] = body.enabled; item.effective[body.field] = body.enabled; item.revision += 'x';
   return {item: structuredClone(item), message: '群开关已保存。'};
 }
 if (endpoint === 'manage/arcade') {
   let item = window.__mg.arcades.find(item => item.id === body.id && item.source === body.source);
   if (body.action === 'create') {
     item = {id: '10000', ...body.fields, person: 0, status: '营业', alias: [], group: [], group_aliases: {}, game_machine_counts: {}, source: 'local', revision: 'v1'};
     window.__mg.arcades.push(item);
   } else if (body.action === 'save') Object.assign(item, body.fields);
   else if (body.action === 'alias') {
     if (body.group) item.group_aliases[body.group] = body.aliases; else item.alias = body.aliases;
   } else if (body.action === 'subscribe') item.group = [...item.group.filter(gid => gid !== Number(body.group)), ...(body.enabled ? [Number(body.group)] : [])];
   else if (body.action === 'delete') {window.__mg.arcades = window.__mg.arcades.filter(item => item.id !== body.id); return {item: null, message: '已删除。'};}
   item.revision += 'x'; return {item: structuredClone(item), message: '机厅设置已保存。'};
 }
 if (endpoint === 'manage/song-alias') {
   const item = window.__mg.songs.find(item => item.id === body.id);
   item.local = body.action === 'add' ? [...item.local, body.alias] : item.local.filter(value => value !== body.alias);
   item.revision += 'x'; return {message: '本地歌曲别名已保存。'};
 }
};
"""


def ready(page):
    page.locator('#ready-label').filter(has_text='待补齐').wait_for()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT / 'views/resources')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors = []
    try:
        with sync_playwright() as playwright:
            options = {'headless': True}
            if args.browser:
                options['executable_path'] = str(args.browser)
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script(BRIDGE + MANAGEMENT_BRIDGE)
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            ready(page)
            page.locator('#tab-groups').click()
            page.locator('#groups-body tr').first.wait_for()
            page.get_by_role('switch', name='200 猜歌', exact=True).check()
            page.wait_for_function("window.__mg.groups[0].guess === true")
            page.locator('#groups-pages').get_by_role('button', name='下一页').click()
            page.wait_for_function("document.querySelector('#groups-pages').textContent.includes('2 / 2')")
            page.locator('#groups-pages').get_by_role('button', name='上一页').click()
            page.wait_for_function("document.querySelector('#groups-pages').textContent.includes('1 / 2')")
            check_layout(page)
            page.screenshot(path=str(args.output / 'management-groups.png'))
            page.locator('#groups-body tr').first.get_by_role('button', name='机厅订阅', exact=True).click()
            page.locator('#arcades-body').get_by_role('button', name='编辑 示例机厅（天街店）', exact=True).click()
            page.locator('#arcade-name').fill('示例机厅（新名称）')
            page.locator('#arcade-save').click()
            page.wait_for_function("window.__mg.arcades[0].name.includes('新名称')")
            page.locator('#arcade-aliases').fill('天街\n新别名')
            page.locator('#arcade-save-aliases').click()
            page.wait_for_function("window.__mg.arcades[0].alias.includes('新别名')")
            page.locator('#arcade-group-aliases').fill('附近店\n本群新别名')
            page.locator('#arcade-save-group-aliases').click()
            page.wait_for_function("window.__mg.arcades[0].group_aliases['200'].includes('本群新别名')")
            page.screenshot(path=str(args.output / 'management-arcade-editor.png'), full_page=True)
            page.locator('#arcade-dialog').get_by_role('button', name='关闭机厅设置').click()
            page.locator('#arcades-subscribed').uncheck()
            page.get_by_role('combobox', name='数据源', exact=True).click()
            page.get_by_role('option', name='nearcade', exact=True).click()
            page.locator('#arcades-body').get_by_role('button', name='编辑 示例云端店', exact=True).click()
            assert page.locator('#arcade-name').get_attribute('readonly') is not None
            assert page.locator('#arcade-save').is_hidden()
            assert page.locator('#arcade-delete').is_hidden()
            page.locator('#arcade-subscribe').click()
            page.wait_for_function("window.__mg.arcades[1].group.includes(200)")
            page.locator('#arcade-dialog').get_by_role('button', name='关闭机厅设置').click()
            page.screenshot(path=str(args.output / 'management-cloud.png'), full_page=True)
            page.locator('#tab-songs').click()
            page.locator('#songs-body').get_by_role('button', name='编辑 にっこり^^調査隊のテーマ 的别名', exact=True).click()
            page.locator('#song-new-alias').fill('测试新别名')
            page.locator('#song-alias-form').get_by_role('button', name='添加', exact=True).click()
            page.wait_for_function("window.__mg.songs[0].local.includes('测试新别名')")
            page.locator('#song-local-aliases').get_by_role('button', name='删除别名 测试新别名', exact=True).click()
            page.locator('#manage-confirm button[value=confirm]').click()
            page.wait_for_function("!window.__mg.songs[0].local.includes('测试新别名')")
            page.evaluate('window.__mgFail = true')
            page.locator('#song-new-alias').fill('失败别名')
            page.locator('#song-alias-form').get_by_role('button', name='添加', exact=True).click()
            page.locator('#song-error').filter(has_text='数据已变化').wait_for()
            assert not page.evaluate("window.__mg.songs[0].local.includes('失败别名')")
            page.locator('#song-dialog').get_by_role('button', name='关闭歌曲别名').click()
            page.screenshot(path=str(args.output / 'management-songs.png'), full_page=True)
            for width in (390, 320):
                page.set_viewport_size({'width': width, 'height': 844})
                for kind in ('groups', 'arcades', 'songs'):
                    page.locator(f'#tab-{kind}').click()
                    page.locator(f'#pane-{kind}').wait_for()
                    page.wait_for_timeout(150)
                    page.screenshot(path=str(args.output / f'management-{kind}-{width}.png'), full_page=True)
                    try:
                        check_layout(page)
                        assert page.evaluate("""[...document.querySelectorAll('.check-field')].filter(item => item.getClientRects().length)
                            .every(item => item.scrollWidth <= item.clientWidth + 1)"""), 'checkbox label overflow'
                    except AssertionError:
                        print(kind, width, page.evaluate("""[...document.querySelectorAll('body *')].filter(element => {
                          const box = element.getBoundingClientRect(); return box.width && box.right > innerWidth + 1;
                        }).map(element => ({tag: element.tagName, id: element.id, class: element.className,
                          right: element.getBoundingClientRect().right, width: element.getBoundingClientRect().width})).slice(0, 20)"""))
                        raise
                    if kind == 'arcades':
                        page.locator('#arcades-body button').first.click()
                        page.locator('#arcade-dialog[open]').wait_for()
                        check_layout(page)
                        page.screenshot(path=str(args.output / f'management-editor-{width}.png'))
                        page.locator('#arcade-dialog').get_by_role('button', name='关闭机厅设置').click()
            page.evaluate('window.__context({isDark: true})')
            page.screenshot(path=str(args.output / 'management-dark-mobile.png'), full_page=True)
            assert not errors, errors
            browser.close()
        print('Management UI QA passed: desktop/mobile, tabs, pagination, group toggle, arcade edit/subscription/aliases, cloud read-only, song aliases and stale-save error.')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    main()
