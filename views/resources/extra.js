import {enhanceSelects} from './select.js';
import {renderPagination} from './pagination.js';
import {copyText} from './clipboard.js';
import {showNotice} from './notice.js';

const $ = id => document.getElementById(id);
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const titles = {mappings: '机台映射', players: '玩家设置', rankings: '群排行榜', pc: 'PC 数据', notices: '更新通知', cache: '缓存维护', limits: '同步额度', tunnel: 'SSH 隧道'};
let kind = 'mappings', page = 1, state = null, busy = false, generation = 0, timer = null, renderedKind = '';
let selected = null, editorRevision = '', dialogKind = '';
let maintenanceKind = 'mappings';

function node(tag, className = '', text) {const item = document.createElement(tag); item.className = className; if (text !== undefined) item.textContent = text; return item;}
function button(icon, label, handler, compact = false) {
  const item = node('button', compact ? 'icon-button' : 'secondary'); item.type = 'button'; item.title = label; item.setAttribute('aria-label', label);
  const img = node('img'); img.src = `./icons/${icon}.svg`; img.alt = ''; item.append(img);
  if (!compact) item.append(document.createTextNode(label)); item.disabled = busy; item.addEventListener('click', handler); return item;
}
function message(text = '', ok = false, modal = false) {showNotice($(modal ? 'extra-error' : 'manage-notice'), text, ok);}
function time(value) {return value ? new Date(value * 1000).toLocaleString('zh-CN', {hour12: false}) : '—';}
function bytes(value) {return value > 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${(value / 1024).toFixed(1)} KB`;}
function shortId(row) {const item = node('div', 'extra-id', row.id.length > 24 ? `${row.id.slice(0, 12)}…${row.id.slice(-8)}` : row.id); item.title = row.id; return item;}
function content(title, detail) {const item = node('div', '', title); if (detail) item.append(node('div', 'cell-detail', detail)); return item;}
function select(id, options, value) {const item = node('select'); item.id = id; for (const option of options) {const child = node('option', '', option.label); child.value = option.value; item.append(child);} item.value = options.some(option => option.value === value) ? value : options[0]?.value ?? ''; return item;}
function field(title, id, value = '', type = 'text', wide = false) {const label = node('label', wide ? 'span-two' : '', title); const input = node('input'); input.id = id; input.type = type; input.value = value; input.maxLength = 150; input.setAttribute('aria-label', title); label.append(input); return label;}
function selectField(title, id, options, value) {const label = node('label', '', title); const input = select(id, options, value); input.setAttribute('aria-label', title); label.append(input); return label;}
async function ask(title, detail, expected) {
  const dialog = $('extra-confirm'); if (dialog.open) return null;
  $('extra-confirm-title').textContent = title; $('extra-confirm-message').textContent = detail;
  $('extra-confirm-input').value = ''; $('extra-confirm-input').placeholder = expected; dialog.returnValue = 'cancel'; dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm' ? $('extra-confirm-input').value.trim() : null), {once: true}));
}
function dialog(title) {message('', false, true); $('extra-dialog-title').textContent = title; $('extra-editor').replaceChildren(); if (!$('extra-dialog').open) $('extra-dialog').showModal();}
function controlsDisabled(value) {
  for (const item of document.querySelectorAll('#pane-more button, #pane-more input, #pane-more select, #extra-editor button, #extra-editor input, #extra-editor select')) item.disabled = value;
  if (!value && $('player-unbind-official')) $('player-unbind-official').disabled = !selected?.official_user_id;
}

async function post(body, modal = false, noticeTarget = null) {
  if (!bridge || busy) return null;
  const report = (text = '', ok = false) => noticeTarget ? showNotice(noticeTarget, text, ok) : message(text, ok, modal);
  busy = true; report(); controlsDisabled(true);
  try {
    const result = await bridge.apiPost('manage/extra', {kind: dialogKind || kind, ...body});
    report(result.message || '操作完成。', true);
    return result;
  } catch (error) {report(error.message || '操作失败。'); return null;}
  finally {busy = false; controlsDisabled(false); enhanceSelects();}
}
async function destructive(row, action, expected, description, extra = {}) {
  const answer = await ask(expected, description, expected); if (answer === null) return;
  const result = await post({id: row.id, revision: row.revision, action, confirm: answer, ...extra});
  if (result) await load();
}
function appendCell(row, title, value) {const cell = node('td'); cell.dataset.label = title; cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? '—'))); row.append(cell);}

function renderTable(data) {
  $('extra-head').closest('table').dataset.kind = kind;
  const columns = {
    mappings: ['映射', '目标', '机台别名', '操作'], players: ['QQ', '查询设置', '授权状态', '操作'],
    rankings: ['玩家', '机器人 / 群', '参与状态', '操作'], pc: ['玩家快照', '更新时间', '成绩记录', '操作'],
    cache: ['缓存文件', '大小', '更新时间', '操作'], notices: ['版本', '更新摘要', '状态', '操作'],
    limits: ['身份记录', '操作', '使用与冷却', '操作'],
  }[kind];
  const header = node('tr'); for (const name of columns) header.append(node('th', '', name)); $('extra-head').replaceChildren(header);
  const body = $('extra-body'); body.replaceChildren();
  for (const row of data.items) {
    const tr = node('tr'), actions = node('div', 'extra-row-actions');
    let values;
    if (kind === 'mappings') {
      values = [content(row.entry, row.valid ? '' : '格式异常'), content(`店铺 ${row.shop || '—'}`, `gameId ${row.game || '自动选择'}`), row.alias || '默认游戏'];
      actions.append(button('pencil', '编辑映射', () => editMapping(row), true), button('trash', '删除映射', () => destructive(row, 'delete', '删除映射', `删除 ${row.entry}？`, {source: $('extra-source').value, revision: state.meta.revision}), true));
    } else if (kind === 'players') {
      const authorization = node('div', 'player-authorization');
      authorization.append(node('div', '', `水鱼：${row.df_cached ? '短期缓存可用' : row.df_configured ? '待主动检测' : '应用未配置'}`),
        node('div', '', `落雪：${row.lxns_bound ? '已存本地授权' : '无本地授权'}`));
      values = [content(row.id, row.official_user_id ? `游戏 userId：${row.official_user_id}` : '未绑定游戏账号'), content(row.service === 'Diving-Fish' ? '水鱼查分器' : '落雪查分器', row.theme), authorization];
      actions.append(button('pencil', '编辑玩家设置', () => editPlayer(row), true));
    } else if (kind === 'rankings') {
      values = [content(row.name || '未记录昵称', `QQ ${row.qq}`), row.scope.replace(':', ' / '), '自愿加入'];
      actions.append(button('trash', '移出排行榜', () => destructive(row, 'remove', '移出排行榜', `移除 ${row.name || row.qq} 在 ${row.scope} 的参与记录？`, {scope: row.scope, qq: row.qq}), true));
    } else if (kind === 'pc') {
      const title = content(row.name); title.append(shortId(row)); values = [title, time(row.updated_at), row.valid ? `${row.count} 条` : '文件异常'];
      const view = button('search', '查看快照成绩', () => viewPC(row), true); view.disabled = !row.valid;
      const remove = button('trash', '删除快照', () => destructive(row, 'delete', '删除快照', `删除 ${row.name} 的本地快照？查分器成绩和额度不受影响。`), true); remove.disabled = !row.valid;
      actions.append(view, remove);
    } else if (kind === 'notices') {
      values = [`v${row.version}`, row.summary, row.version === data.meta.current.current ? '当前版本' : '历史版本'];
      actions.append(button('search', `查看 v${row.version} 更新`, () => {
        dialogKind = ''; selected = null; dialog(`v${row.version} · 更新内容`);
        $('extra-editor').append(node('pre', 'extra-preview notice-full-preview', row.notes));
      }, true));
    } else if (kind === 'cache') {
      values = [content(row.name, row.exists ? row.clearable ? '派生缓存' : '原始离线缓存' : '文件不存在'), bytes(row.bytes), time(row.updated_at)];
      const validate = button('search', '校验缓存 JSON', async () => {if (await post({id: row.id, action: 'validate', revision: row.revision})) await load();}, true); validate.disabled = !row.exists;
      actions.append(validate);
      if (row.clearable) actions.append(button('trash', '清理派生缓存', () => destructive(row, 'clear', '清理派生缓存', `删除 ${row.name}？原始离线缓存和图片素材保留。`), true));
    } else {
      values = [shortId(row), row.action === 'fetch' ? '官方取数' : '查分器上传', content(`今日 ${row.count} / ${row.daily_limit || '不限'}`, `剩余冷却 ${row.remaining} 秒 · 记录日 ${row.day}`)];
      actions.append(button('refresh-cw', '重置额度', () => destructive(row, row.action, '重置额度', '重置此身份所选操作的每日次数与冷却？不会清理其他身份。'), true));
    }
    values.forEach((value, index) => appendCell(tr, columns[index], value)); appendCell(tr, columns[3], actions); body.append(tr);
  }
  if (!data.items.length) {const tr = node('tr'); const cell = node('td', 'empty-cell', '暂无匹配记录'); cell.colSpan = columns.length; tr.append(cell); body.append(tr);}
}

function renderRows(data) {
  $('extra-table-wrap').hidden = kind === 'tunnel';
  if (kind === 'tunnel') {
    for (const suffix of ['-top', '']) {$(`extra-pages${suffix}`).hidden = true; $(`extra-pages${suffix}`).replaceChildren();}
    return;
  }
  const notices = kind === 'notices';
  renderTable(data);
  for (const suffix of ['-top', '']) {
    const target = $(`extra-pages${suffix}`);
    target.hidden = notices && data.pages <= 1;
    if (target.hidden) {target.replaceChildren(); continue;}
    renderPagination(target, data, async number => {
      const previousKind = kind; page = number; await load();
      if (!$('pane-more').hidden && kind === previousKind) $('extra-pages-top').scrollIntoView({block: 'nearest'});
    }, {disabled: busy, label: titles[kind], unit: notices ? '个版本' : '条'});
  }
}

function renderMeta(data) {
  $('extra-title').textContent = titles[kind]; $('extra-source-label').hidden = kind !== 'mappings'; $('extra-add').hidden = kind !== 'mappings';
  $('extra-search').hidden = ['cache', 'tunnel'].includes(kind);
  const actions = $('extra-actions'); actions.replaceChildren(); const meta = $('extra-meta'); meta.replaceChildren();
  if (kind === 'tunnel') renderTunnel(data.meta, meta);
  if (kind === 'limits') actions.append(button('pencil', '限流设置', editLimitSettings));
  if (kind === 'cache') {
    actions.append(button('refresh-cw', '刷新曲库与别名', async () => {if (await post({action: 'refresh_music'})) await load();}),
      button('refresh-cw', '刷新别名', async () => {if (await post({action: 'refresh_aliases'})) await load();}),
      button('trash', '清理帮助图缓存', async () => {const answer = await ask('清理帮助图缓存', '下一次查询会重新生成帮助图。', '清理帮助缓存'); if (answer !== null && await post({action: 'clear_help', confirm: answer})) await load();}));
  }
  if (kind === 'players') meta.append(node('p', 'source-note', '水鱼绑定由服务端管理；清除短期缓存不解除绑定。落雪清除操作仅删除本地授权。'));
  if (kind === 'notices') {
    const section = node('section', 'extra-meta-section'); section.append(node('h3', '', '当前版本通知'));
    const current = data.meta.current, status = node('dl', 'notice-state');
    for (const [label, value] of [['当前版本', `v${current.current}`], ['记录版本', current.recorded], ['自动通知', current.pending ? `待处理 · 已投递 ${current.delivered.length} 群 · 已尝试 ${current.attempts} 次` : '无待处理任务']]) {
      const item = node('div'); item.append(node('dt', '', label), node('dd', '', value)); status.append(item);
    }
    section.append(status, button('search', '预览完整通知', () => {
      dialogKind = ''; selected = null; dialog(`v${current.current} · 完整通知预览`);
      $('extra-editor').append(node('pre', 'extra-preview notice-full-preview', data.meta.preview));
    }));
    const form = node('form', 'manage-toolbar'); form.append(selectField('OneBot 平台', 'notice-platform', data.meta.platforms || [], ''), field('目标群号', 'notice-group'));
    const send = button('upload', '发送到指定群', async () => {
      const group = $('notice-group').value.trim();
      const answer = await ask('发送版本通知', `向群 ${group} 发送当前版本更新内容？仅允许已明确开启舞萌功能的群。请输入群号确认。`, group);
      if (answer !== null && await post({action: 'send', group, platform: $('notice-platform').value, confirm: answer, revision: state.meta.revision})) await load();
    }); send.disabled = !data.meta.platforms?.length; form.append(send); form.addEventListener('submit', event => event.preventDefault()); section.append(form);
    if (data.meta.audit?.length) {const list = node('div', 'extra-audit'); for (const log of [...data.meta.audit].reverse()) list.append(node('div', '', `${time(log.at)} · ${log.platform} / ${log.group} · ${log.status === 'success' ? '已发送' : '未确认'}`)); section.append(list);}
    meta.append(section);
  }
  const job = data.job; $('extra-job').hidden = kind !== 'cache' || !job || job.phase === 'idle'; $('extra-job').textContent = job?.message || '';
}

async function load() {
  if (!bridge || $('pane-more').hidden) return;
  const current = ++generation; clearTimeout(timer);
  if (renderedKind !== kind) {
    renderedKind = kind; state = null;
    $('extra-title').textContent = titles[kind]; $('extra-head').replaceChildren(); $('extra-body').replaceChildren();
    $('extra-actions').replaceChildren(); $('extra-meta').replaceChildren(); $('extra-pages').replaceChildren(); $('extra-pages-top').replaceChildren();
    $('extra-pages').hidden = true; $('extra-pages-top').hidden = true;
    $('extra-add').hidden = true; $('extra-source-label').hidden = kind !== 'mappings'; $('extra-search').hidden = ['cache', 'tunnel'].includes(kind);
    $('extra-table-wrap').hidden = kind === 'tunnel';
    const placeholders = {mappings: '映射 / 机台别名 / ID', players: 'QQ 号', rankings: '玩家昵称 / QQ / 群号', pc: '玩家昵称 / 快照编号', limits: '身份记录编号', notices: '版本号 / 更新内容'};
    $('extra-q').placeholder = placeholders[kind] || '';
    $('extra-q').setAttribute('aria-label', placeholders[kind] || '搜索管理记录');
    $('extra-search').dataset.kind = kind;
  }
  try {
    const data = await bridge.apiGet('manage/extra', {kind, q: kind === 'cache' ? '' : $('extra-q').value.trim(), source: $('extra-source').value, page});
    if (current !== generation) return;
    if (!Array.isArray(data.items)) throw new Error('管理模块尚未就绪，请重载插件。');
    state = data; page = data.page; renderRows(data); renderMeta(data); enhanceSelects();
    if (kind === 'cache' && data.job?.phase === 'running') timer = setTimeout(load, 1500);
  } catch (error) {message(error.message || '读取管理数据失败。');}
}

function renderTunnel(environment, target) {
  const status = node('dl', 'notice-state');
  for (const [label, value] of [['部署系统', `${environment.system} / ${environment.architecture}`], ['运行方式', environment.container ? '容器' : '直接运行']]) {
    const item = node('div'); item.append(node('dt', '', label), node('dd', '', value)); status.append(item);
  }
  target.append(status);
  if (environment.warning) target.append(node('p', 'extra-alert', environment.warning));
  target.append(node('p', 'source-note', '在可正常连接官方服务的 Windows 电脑运行，需 Python 3.10+、OpenSSH 和 SSH 私钥。保持电脑联网、窗口开启；服务器需 Python 3.10+，无需开放额外公网端口。'));
  const form = node('form', 'extra-editor-form tunnel-form');
  form.append(field('SSH 用户@服务器地址', 'tunnel-server', `ubuntu@${location.hostname}`),
    field('SSH 端口', 'tunnel-ssh-port', '22', 'number'),
    field('服务器入口 IPv4', 'tunnel-bridge', environment.bridge),
    field('AstrBot 来源 IPv4', 'tunnel-source', environment.source),
    field('服务器入口端口', 'tunnel-bridge-port', '17890', 'number'),
    field('SSH 回环端口', 'tunnel-remote-port', '17891', 'number'));
  const output = node('section', 'tunnel-output'); output.hidden = true;
  const label = node('label', 'tunnel-script-label', '本机 PowerShell 连接脚本');
  const script = node('textarea', 'tunnel-script'); script.id = 'tunnel-script'; script.readOnly = true; script.rows = 12; script.spellcheck = false;
  label.append(script);
  const proxy = node('code', 'tunnel-proxy'); proxy.id = 'tunnel-proxy';
  const config = node('div', 'tunnel-config'); config.append(node('span', '', '官方取数专用代理'), proxy);
  const copy = (text, success) => async () => {
    try {await copyText(text()); message(success, true);}
    catch {message('浏览器禁止复制，请在脚本或代理地址中手动选择文本复制。'); script.focus(); script.select();}
  };
  const outputActions = node('div', 'extra-editor-actions');
  outputActions.append(button('copy', '复制连接脚本', copy(() => script.value, '连接脚本已复制，请在电脑的 PowerShell 中运行。')),
    button('copy', '复制代理地址', copy(() => proxy.textContent, '代理地址已复制，连接成功后填入插件配置并重载插件。')));
  output.append(config, label, outputActions,
    node('p', 'source-note', '首次连接请核对服务器指纹。脚本仅安装 SSH 用户目录下的辅助文件，不修改插件配置或系统路由；连接断开后自动重试，关闭窗口即停止。容器重建后请重新生成。'));
  const generationNotice = node('div', 'notice tunnel-notice'); generationNotice.id = 'tunnel-generation-notice';
  generationNotice.setAttribute('role', 'status'); generationNotice.hidden = true;
  const generate = button('settings', '生成连接脚本', async () => {
    output.hidden = true;
    const result = await post({action: 'generate', server: $('tunnel-server').value.trim(), bridge: $('tunnel-bridge').value.trim(),
      source: $('tunnel-source').value.trim(), ssh_port: $('tunnel-ssh-port').value,
      bridge_port: $('tunnel-bridge-port').value, remote_port: $('tunnel-remote-port').value}, false, generationNotice);
    if (result && kind === 'tunnel' && target.contains(output)) {
      script.value = result.script; proxy.textContent = result.proxy; output.hidden = false;
    }
  });
  const actions = node('div', 'extra-editor-actions'); actions.append(generate); form.append(actions, generationNotice);
  form.addEventListener('submit', event => {event.preventDefault(); generate.click();});
  form.addEventListener('input', () => {output.hidden = true; showNotice(generationNotice);});
  target.append(form, output);
}

function editMapping(row = null) {
  selected = row; dialogKind = 'mappings'; editorRevision = state.meta.revision; dialog(row ? '编辑机台映射' : '添加机台映射');
  const source = $('extra-source').value, form = node('form', 'extra-editor-form');
  form.append(field(source === 'local' ? '本地机厅 ID' : '云端店铺 ID', 'mapping-local', row?.local || ''), field('机台别名', 'mapping-alias', row?.alias || ''));
  const shop = field('云端店铺 ID', 'mapping-shop', row?.shop || ''); shop.hidden = source !== 'local';
  form.append(shop, field('gameId', 'mapping-game', row?.game || ''));
  const lookup = node('div', 'extra-lookup'); const actions = node('div', 'extra-editor-actions');
  actions.append(button('search', '查询店铺机台', async () => {
    try {
      const data = await bridge.apiGet('manage/detail', {kind: 'mappings', shop: $(source === 'local' ? 'mapping-shop' : 'mapping-local').value.trim()});
      lookup.replaceChildren(node('h3', '', data.name));
      for (const item of data.items) {const line = node('div', 'extra-lookup-row'); line.append(content(`${item.name} ${item.version}`, `gameId ${item.id} · 数量 ${item.quantity}`), button('plus', '选择机台', () => {$('mapping-game').value = item.id;})); lookup.append(line);}
    } catch (error) {message(error.message, false, true);}
  }));
  const save = button('save', '保存映射', async () => {
    const result = await post({kind: 'mappings', action: 'save', source, id: selected?.id, revision: editorRevision,
      local: $('mapping-local').value.trim(), alias: $('mapping-alias').value.trim(), shop: $('mapping-shop').value.trim(), game: $('mapping-game').value.trim()}, true);
    if (result) {$('extra-dialog').close(); dialogKind = ''; await load();}
  }); actions.append(save); form.append(actions, lookup); form.addEventListener('submit', event => {event.preventDefault(); save.click();}); $('extra-editor').append(form);
}

function editPlayer(row) {
  selected = row; dialogKind = 'players'; dialog(`玩家 ${row.id}`);
  const status = node('div', 'extra-readonly');
  const updateStatus = item => {
    status.replaceChildren(node('span', '', `水鱼：${item.df_cached ? '短期缓存可用' : item.df_configured ? '待主动检测' : '应用未配置'}`),
      node('span', '', `落雪：${item.lxns_bound ? '已存本地授权' : '无本地授权'}`),
      node('span', '', item.official_user_id ? `游戏账号 userId：${item.official_user_id}` : '游戏账号：未绑定'));
    const unbind = $('player-unbind-official'); if (unbind) unbind.disabled = !item.official_user_id;
  }; updateStatus(row);
  const form = node('form', 'extra-editor-form');
  const services = state.meta.services.map(item => ({...item, label: item.value === 'Diving-Fish' ? '水鱼查分器' : item.value === 'Lxns-Network' ? '落雪查分器' : item.label}));
  form.append(selectField('数据源', 'player-service', services, row.service), selectField('绘图主题', 'player-theme', state.meta.themes, row.theme));
  async function action(name, expected) {
    const description = name === 'unbind_official' ? `解绑 QQ ${row.id} 的游戏账号？查分器授权和本地快照保留；再次更新可重新绑定，旧快照不能直接重传。` : `对 QQ ${row.id} 执行「${expected}」？不会撤销查分器服务端授权。`;
    const answer = expected ? await ask(expected, description, expected) : '';
    if (answer === null) return;
    const result = await post({kind: 'players', action: name, id: row.id, revision: selected.revision, confirm: answer,
      official_revision: selected.official_revision, service: $('player-service').value, theme: $('player-theme').value}, true);
    if (result) {selected = result.item; updateStatus(selected); await load();}
  }
  const actions = node('div', 'extra-editor-actions player-actions'); actions.append(button('save', '保存设置', () => action('save')),
    button('search', '检测水鱼授权', () => action('check_df')), button('trash', '清除水鱼短期缓存', () => action('clear_df_cache', '清除短期缓存')),
    button('trash', '清除落雪本地授权', () => action('unlink_lxns', '清除本地授权')));
  const unbind = button('trash', '解绑游戏账号', () => action('unbind_official', '解绑游戏账号'));
  unbind.id = 'player-unbind-official'; unbind.disabled = !row.official_user_id; actions.append(unbind);
  actions.firstElementChild.className = 'primary';
  form.append(actions); form.addEventListener('submit', event => {event.preventDefault(); action('save');}); $('extra-editor').append(status, form); enhanceSelects();
}

function editLimitSettings() {
  dialogKind = 'limits'; editorRevision = state.meta.revision; dialog('同步限流设置');
  const form = node('form', 'extra-editor-form');
  const labels = {official_fetch_cooldown: '官方取数冷却（秒）', official_fetch_daily_limit: '每日取数次数', official_upload_cooldown: '上传冷却（秒）', official_upload_daily_limit: '每日上传次数'};
  for (const [key, label] of Object.entries(labels)) {const item = field(label, key, state.meta.settings[key], 'number'); item.querySelector('input').min = '0'; item.querySelector('input').max = '1000000'; item.querySelector('input').step = '1'; form.append(item);}
  const actions = node('div', 'extra-editor-actions'); actions.append(node('span', 'muted', '0 关闭对应限制'));
  const save = button('save', '保存限流设置', async () => {
    if (Object.keys(labels).some(key => !$(key).value.trim() || !Number.isInteger(Number($(key).value)))) return message('请填写完整的非负整数设置。', false, true);
    const settings = Object.fromEntries(Object.keys(labels).map(key => [key, Number($(key).value)]));
    if (await post({kind: 'limits', action: 'settings', revision: editorRevision, settings}, true)) {$('extra-dialog').close(); dialogKind = ''; await load();}});
  actions.append(save); form.append(actions); form.addEventListener('submit', event => {event.preventDefault(); save.click();}); $('extra-editor').append(form);
}

function viewPC(row) {
  selected = row; dialogKind = ''; dialog(`${row.name} · 本地成绩`);
  const target = $('extra-editor'); let number = 1;
  const toolbar = node('form', 'manage-toolbar'); toolbar.append(field('歌曲 ID / 名称', 'pc-filter'));
  const search = button('search', '查询快照', () => {number = 1; retrieve();}); toolbar.append(search); toolbar.addEventListener('submit', event => {event.preventDefault(); search.click();});
  const wrapper = node('div', 'manage-table-wrap'), table = node('table', 'manage-table extra-detail-table'), head = node('thead'), tr = node('tr');
  ['歌曲', '难度', '达成率', 'DX 分', 'PC'].forEach(value => tr.append(node('th', '', value))); head.append(tr); const body = node('tbody'); table.append(head, body); wrapper.append(table);
  const paginationTop = node('div', 'extra-detail-pager pagination-top'), pagination = node('div', 'extra-detail-pager'); target.append(toolbar, paginationTop, wrapper, pagination);
  let request = 0;
  async function retrieve() {
    const current = ++request;
    const keyword = $('pc-filter').value.trim();
    try {
      const data = await bridge.apiGet('manage/detail', {kind: 'pc', id: row.id, page: number, q: keyword});
      if (current !== request || !$('extra-dialog').open || selected !== row || !target.contains(toolbar)) return;
      number = data.page; body.replaceChildren();
      for (const score of data.items) {const item = node('tr'); [content(score.name, `ID ${score.id}`), ['绿', '黄', '红', '紫', '白'][score.difficulty] || String(score.difficulty), `${score.achievements.toFixed(4)}%`, score.dx_score, score.pc ?? '—'].forEach((value, index) => appendCell(item, '', value)); body.append(item);}
      for (const pager of [paginationTop, pagination]) renderPagination(pager, data, async value => {
        number = value; await retrieve();
        if (target.contains(paginationTop) && $('extra-dialog').open) paginationTop.scrollIntoView({block: 'nearest'});
      }, {label: 'PC 成绩'});
    } catch (error) {if (current === request && target.contains(toolbar)) message(error.message, false, true);}
  }
  retrieve();
}

function choose(value) {
  kind = value; page = 1; $('extra-q').value = ''; dialogKind = ''; message();
  $('extra-content').setAttribute('role', value === 'players' ? 'region' : 'tabpanel');
  $('extra-content').setAttribute('aria-labelledby', value === 'players' ? 'extra-title' : `extra-tab-${value}`);
  for (const tab of $('extra-tabs').children) {
    const active = tab.id === `extra-tab-${value}`;
    tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
  }
  load();
}
for (const [value, title] of Object.entries(titles).filter(([value]) => value !== 'players')) {
  const item = node('button', '', title); item.type = 'button'; item.id = `extra-tab-${value}`;
  item.setAttribute('role', 'tab'); item.setAttribute('aria-controls', 'extra-content');
  item.setAttribute('aria-selected', String(value === kind)); item.tabIndex = value === kind ? 0 : -1;
  item.addEventListener('click', () => {maintenanceKind = value; choose(value);});
  item.addEventListener('keydown', event => {
    const tabs = [...$('extra-tabs').children], index = tabs.indexOf(item);
    const target = {ArrowLeft: (index + tabs.length - 1) % tabs.length, ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1}[event.key];
    if (target === undefined) return;
    event.preventDefault(); tabs[target].focus(); tabs[target].click();
  });
  $('extra-tabs').append(item);
}
$('extra-search').addEventListener('submit', event => {event.preventDefault(); page = 1; load();});
$('extra-source').addEventListener('change', () => {page = 1; load();}); $('extra-refresh').addEventListener('click', load);
$('extra-add').addEventListener('click', () => editMapping());
for (const id of ['extra-dialog-close', 'extra-editor-close']) $(id).addEventListener('click', () => {$('extra-dialog').close(); dialogKind = '';});
$('extra-dialog').addEventListener('close', () => {dialogKind = '';});
window.addEventListener('mai:navigate', event => {
  generation++; clearTimeout(timer);
  if (!['more', 'players'].includes(event.detail)) return;
  $('extra-tabs').hidden = event.detail !== 'more';
  choose(event.detail === 'more' ? maintenanceKind : event.detail);
});
window.addEventListener('beforeunload', () => clearTimeout(timer));
