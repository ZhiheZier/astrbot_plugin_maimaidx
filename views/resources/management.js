import {renderPagination} from './pagination.js';
import {showNotice} from './notice.js';

const $ = id => document.getElementById(id);
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const kinds = ['resources', 'groups', 'arcades', 'songs', 'players', 'more'];
const sharedKinds = ['more', 'players'];
const contexts = {resources: '资源与素材', groups: '群功能与开关', arcades: '机厅资料与群订阅', songs: '歌曲本地别名', players: '玩家偏好与授权管理', more: '数据与维护'};
const pages = {groups: 1, arcades: 1, songs: 1};
const generations = {groups: 0, arcades: 0, songs: 0};
let active = 'resources';
let busy = false;
let selectedArcade = null;
let selectedSong = null;
let metadataLoaded = false;

function node(tag, className = '', text) {
  const item = document.createElement(tag);
  item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function button(icon, title, handler, compact = true) {
  const item = node('button', compact ? 'icon-button' : 'secondary');
  item.type = 'button'; item.title = title; item.setAttribute('aria-label', title);
  const image = node('img'); image.src = `./icons/${icon}.svg`; image.alt = '';
  item.append(image);
  if (!compact) item.append(document.createTextNode(title));
  item.disabled = busy;
  item.addEventListener('click', handler);
  return item;
}

function message(value = '', ok = false, dialog) {
  const output = dialog ? $(dialog === 'arcade' ? 'arcade-error' : 'song-error') : $('manage-notice');
  showNotice(output, value, ok);
}

async function confirm(title, detail, required = '') {
  const dialog = $('manage-confirm');
  if (dialog.open) return null;
  $('manage-confirm-title').textContent = title;
  $('manage-confirm-message').textContent = detail;
  $('manage-confirm-label').hidden = !required;
  $('manage-confirm-input').value = '';
  $('manage-confirm-input').placeholder = required;
  dialog.returnValue = 'cancel'; dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => {
    resolve(dialog.returnValue === 'confirm' ? (required ? $('manage-confirm-input').value.trim() : true) : null);
  }, {once: true}));
}

function queries(kind) {
  if (kind === 'groups') return {q: $('groups-q').value.trim(), page: pages.groups};
  if (kind === 'songs') return {q: $('songs-q').value.trim(), local_only: $('songs-local').checked ? '1' : '0', page: pages.songs};
  return {q: $('arcades-q').value.trim(), source: $('arcades-source').value,
    group: $('arcades-group').value.trim(), subscribed: $('arcades-subscribed').checked ? '1' : '0', page: pages.arcades};
}

function pagination(kind, data) {
  pages[kind] = data.page;
  for (const suffix of ['-top', '']) renderPagination($(`${kind}-pages${suffix}`), data, async number => {
    pages[kind] = number; await load(kind);
    if (active === kind) $(`${kind}-pages-top`).scrollIntoView({block: 'nearest'});
  }, {disabled: busy, label: contexts[kind]});
}

function cell(row, label, child) {
  const item = node('td'); item.dataset.label = label;
  item.append(typeof child === 'string' ? document.createTextNode(child) : child); row.append(item);
  return item;
}

function aliases(values, remove) {
  const list = node('div', 'alias-list');
  if (!values.length) list.append(node('span', 'muted', '无'));
  for (const value of values) {
    const tag = node('span', 'alias-token'); tag.append(node('span', '', value));
    if (remove) tag.append(button('x', `删除别名 ${value}`, () => remove(value)));
    list.append(tag);
  }
  return list;
}

function empty(body, columns, text) {
  const row = node('tr'); const item = node('td', 'empty-cell', text); item.colSpan = columns;
  row.append(item); body.append(row);
}

function renderGroups(data) {
  const modes = data.modes || {};
  $('group-modes').replaceChildren(...[
    `舞萌白名单：${modes.maimai_whitelist ? '开' : '关'}`,
    `别名白名单：${modes.alias_whitelist ? '开' : '关'}`,
    `别名推送总开关：${modes.alias_global ? '开' : '关'}`,
  ].map(text => node('span', '', text)));
  const body = $('groups-body'); body.replaceChildren();
  for (const item of data.items) {
    const row = node('tr'); cell(row, '群号', item.id);
    for (const [key, title] of [['maimai', '舞萌功能'], ['arcade', '排卡'], ['guess', '猜歌'], ['alias', '别名推送']]) {
      const configured = key === 'maimai' ? item.effective.maimai : key === 'alias' ?
        (modes.alias_whitelist ? item.alias : !item.alias_disabled) : item[key];
      const wrapper = node('div'); const label = node('label', 'switch-control');
      const input = node('input'); input.type = 'checkbox'; input.checked = configured; input.disabled = busy;
      input.setAttribute('role', 'switch'); input.setAttribute('aria-label', `${item.id} ${title}`);
      label.append(input, node('span', '', configured ? '开启' : '关闭')); wrapper.append(label);
      let status = item.effective[key] ? '生效' : '未生效';
      if (key === 'maimai' && item.effective.maimai && !item.maimai) status = '默认允许';
      if (configured && !item.effective[key]) status = key === 'alias' ? '受推送总开关限制' : '受舞萌开关限制';
      wrapper.append(node('div', 'cell-detail', status));
      input.addEventListener('change', async () => {
        await commit('manage/group', {id: item.id, field: key, enabled: input.checked, revision: item.revision});
        await load('groups');
      });
      cell(row, title, wrapper);
    }
    cell(row, '机厅订阅', button('map-pin', '机厅订阅', () => {
      $('arcades-group').value = item.id; $('arcades-subscribed').checked = true;
      pages.arcades = 1; switchTab('arcades');
    })); body.append(row);
  }
  if (!data.items.length) empty(body, 6, '暂无群设置');
}

function renderArcades(data) {
  const body = $('arcades-body'); body.replaceChildren();
  $('arcade-add').hidden = data.source !== 'local'; $('arcades-remote').hidden = data.source !== 'nearcade';
  $('arcades-source-note').textContent = data.warning || (data.source === 'nearcade' ?
    'nearcade 店铺资料与人数只读；可管理本地订阅、全局别名和群别名。' : '本地机厅资料；人数只读。');
  for (const item of data.items) {
    const row = node('tr');
    const name = node('div', '', item.name); name.append(node('div', 'cell-detail', `ID ${item.id} · ${item.source}`), node('div', 'cell-detail', item.location));
    cell(row, '机厅', name);
    const count = node('div', '', String(item.num));
    count.append(node('div', 'cell-detail', `本地人数 ${item.person} · ${item.status}`));
    cell(row, '舞萌机台', count);
    const group = $('arcades-group').value.trim();
    cell(row, '订阅群', group ? `${item.subscribed ? '本群已订阅' : '本群未订阅'} · 共 ${item.group.length} 群` : `${item.group.length} 个群`);
    cell(row, '别名', aliases([...item.alias, ...(item.group_aliases[group] || [])]));
    cell(row, '操作', button('pencil', `编辑 ${item.name}`, () => openArcade(item)));
    body.append(row);
  }
  if (!data.items.length) empty(body, 5, data.source === 'nearcade' ? '暂无匹配缓存，可填写关键词后查询云端' : '暂无匹配机厅');
}

function renderSongs(data) {
  const body = $('songs-body'); body.replaceChildren();
  for (const item of data.items) {
    const row = node('tr'); const name = node('div', '', item.name);
    name.append(node('div', 'cell-detail', `ID ${item.id} · ${item.type}`));
    cell(row, '歌曲', name); cell(row, '本地别名', aliases(item.local)); cell(row, '其他已合并别名', aliases(item.other));
    cell(row, '操作', button('pencil', `编辑 ${item.name} 的别名`, () => openSong(item))); body.append(row);
  }
  if (!data.items.length) empty(body, 4, '暂无匹配歌曲');
}

async function load(kind, extra = {}) {
  if (!bridge) return;
  const generation = ++generations[kind];
  $(`pane-${kind}`).setAttribute('aria-busy', 'true');
  try {
    const data = await bridge.apiGet(`manage/${kind}`, {...queries(kind), ...extra});
    if (generation !== generations[kind]) return;
    if (!Array.isArray(data.items)) throw new Error('管理数据格式不正确，请重载插件后再试。');
    ({groups: renderGroups, arcades: renderArcades, songs: renderSongs}[kind])(data); pagination(kind, data);
  } catch (error) { message(error.message || '读取管理数据失败。'); }
  finally { if (generation === generations[kind]) $(`pane-${kind}`).removeAttribute('aria-busy'); }
}

async function commit(endpoint, body, dialog) {
  if (!bridge || busy) return null;
  busy = true; message('', false, dialog);
  const controls = [...document.querySelectorAll('.manage-pane button, .manage-pane input, .manage-pane select, .editor-dialog button:not(.close-editor), .editor-dialog input, .editor-dialog textarea')];
  const previous = controls.map(item => item.disabled); controls.forEach(item => {item.disabled = true;});
  try {
    const result = await bridge.apiPost(endpoint, body);
    message(result.message || '已保存。', true, dialog);
    return result;
  } catch (error) { message(error.message || '保存失败，原设置未修改。', false, dialog); return null; }
  finally { busy = false; controls.forEach((item, index) => {item.disabled = previous[index];}); }
}

async function switchTab(kind) {
  if (!kinds.includes(kind)) return;
  active = kind;
  for (const value of ['resources', 'groups', 'arcades', 'songs', 'more']) {
    $(`pane-${value}`).hidden = value === 'more' ? !sharedKinds.includes(kind) : value !== kind;
  }
  for (const value of kinds) {
    $(`tab-${value}`).setAttribute('aria-selected', String(value === kind));
    $(`tab-${value}`).tabIndex = value === kind ? 0 : -1;
  }
  $('page-context').textContent = contexts[kind];
  document.title = `${contexts[kind]} · 舞萌 DX`;
  $(`tab-${kind}`).scrollIntoView({block: 'nearest', inline: 'nearest'});
  if (sharedKinds.includes(kind)) $('pane-more').setAttribute('aria-labelledby', `tab-${kind}`);
  message();
  window.dispatchEvent(new CustomEvent('mai:navigate', {detail: kind}));
  if (sharedKinds.includes(kind)) return;
  if (kind !== 'resources') {
    if (!metadataLoaded) {
      try {
        const metadata = await bridge.apiGet('manage/status');
        $('arcades-source').value = metadata.arcade_source || 'local'; metadataLoaded = true;
      } catch (error) { message(error.message || '管理模块尚未就绪。'); return; }
    }
    await load(kind);
  }
}

for (const kind of kinds) {
  $(`tab-${kind}`).addEventListener('click', () => switchTab(kind));
  $(`tab-${kind}`).addEventListener('keydown', event => {
    let index = kinds.indexOf(active);
    if (event.key === 'ArrowRight') index = (index + 1) % kinds.length;
    else if (event.key === 'ArrowLeft') index = (index + kinds.length - 1) % kinds.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = kinds.length - 1;
    else return;
    event.preventDefault(); $(`tab-${kinds[index]}`).focus(); switchTab(kinds[index]);
  });
}
for (const kind of ['groups', 'arcades', 'songs']) {
  $(`${kind}-search`).addEventListener('submit', event => {event.preventDefault(); pages[kind] = 1; load(kind);});
}
$('groups-refresh').addEventListener('click', () => load('groups'));
$('songs-refresh').addEventListener('click', () => load('songs'));
$('songs-local').addEventListener('change', () => {pages.songs = 1; load('songs');});
$('arcades-source').addEventListener('change', () => {pages.arcades = 1; load('arcades');});
$('arcades-remote').addEventListener('click', () => {pages.arcades = 1; load('arcades', {remote: '1'});});
for (const item of document.querySelectorAll('.close-editor')) item.addEventListener('click', () => item.closest('dialog').close());

function groupEditor() {
  const gid = $('arcade-group').value.trim();
  const valid = /^[1-9][0-9]{0,19}$/.test(gid);
  const subscribed = valid && selectedArcade?.group.includes(Number(gid));
  $('arcade-subscribe').textContent = subscribed ? '取消订阅' : '订阅';
  $('arcade-subscription-state').textContent = valid ? (subscribed ? '该群已订阅' : '该群未订阅') : '尚未选择群';
  $('arcade-group-aliases').value = selectedArcade?.group_aliases[gid]?.join('\n') || '';
  $('arcade-subscribe').disabled = !valid || busy;
  $('arcade-save-group-aliases').disabled = !valid || busy;
}

function openArcade(item = null) {
  selectedArcade = item ? structuredClone(item) : null;
  const cloud = item?.source === 'nearcade'; message('', false, 'arcade');
  $('arcade-title').textContent = item ? item.name : '添加本地机厅';
  $('arcade-subtitle').textContent = item ? `ID ${item.id} · ${item.source}${cloud ? ' · 店铺资料只读' : ''}` : '本地 ID 自动分配';
  for (const field of ['name', 'location', 'province', 'mall', 'num']) {
    $(`arcade-${field}`).value = item?.[field] ?? (field === 'num' ? 1 : '');
    $(`arcade-${field}`).readOnly = cloud;
  }
  $('arcade-save').hidden = cloud; $('arcade-delete').hidden = !item || cloud;
  $('arcade-alias-section').hidden = !item; $('arcade-group-section').hidden = !item;
  $('arcade-aliases').value = item?.alias.join('\n') || '';
  $('arcade-group').value = $('arcades-group').value.trim();
  $('arcade-subscribers').textContent = item ? `已订阅群：${item.group.join('、') || '无'}` : '';
  groupEditor(); $('arcade-dialog').showModal();
}
$('arcade-add').addEventListener('click', () => openArcade());
$('arcade-group').addEventListener('input', groupEditor);

async function arcadeCommit(patch) {
  const result = await commit('manage/arcade', {source: selectedArcade?.source || 'local',
    id: selectedArcade?.id, revision: selectedArcade?.revision, ...patch}, 'arcade');
  if (result) {
    selectedArcade = result.item;
    if (patch.action === 'delete') $('arcade-dialog').close();
    else {
      $('arcade-title').textContent = selectedArcade.name;
      $('arcade-subtitle').textContent = `ID ${selectedArcade.id} · ${selectedArcade.source}`;
      $('arcade-alias-section').hidden = false; $('arcade-group-section').hidden = false;
      $('arcade-delete').hidden = selectedArcade.source !== 'local';
      $('arcade-subscribers').textContent = `已订阅群：${selectedArcade.group.join('、') || '无'}`;
      if (patch.action === 'subscribe') groupEditor();
    }
    await load('arcades');
  }
  return result;
}
$('arcade-fields').addEventListener('submit', event => {
  event.preventDefault();
  const fields = Object.fromEntries(['name', 'location', 'province', 'mall'].map(key => [key, $(`arcade-${key}`).value.trim()]));
  fields.num = Number($('arcade-num').value);
  arcadeCommit({action: selectedArcade ? 'save' : 'create', fields});
});
const lines = id => $(id).value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
$('arcade-save-aliases').addEventListener('click', () => arcadeCommit({action: 'alias', aliases: lines('arcade-aliases')}));
$('arcade-save-group-aliases').addEventListener('click', () => arcadeCommit({action: 'alias', group: $('arcade-group').value.trim(), aliases: lines('arcade-group-aliases')}));
$('arcade-subscribe').addEventListener('click', async () => {
  const group = $('arcade-group').value.trim();
  const enabled = !selectedArcade.group.includes(Number(group));
  if (!enabled && !await confirm('取消机厅订阅', `取消群 ${group} 对「${selectedArcade.name}」的订阅？已有群别名保留。`)) return;
  await arcadeCommit({action: 'subscribe', group, enabled});
});
$('arcade-delete').addEventListener('click', async () => {
  const id = selectedArcade.id;
  const answer = await confirm('删除本地机厅', `删除「${selectedArcade.name}」及其订阅、别名和本地报数。请输入 ID ${id} 确认。`, id);
  if (answer !== null) await arcadeCommit({action: 'delete', confirm: answer});
});

function renderSongEditor() {
  $('song-title').textContent = selectedSong.name;
  $('song-subtitle').textContent = `ID ${selectedSong.id} · ${selectedSong.type}`;
  $('song-local-aliases').replaceChildren(aliases(selectedSong.local, async value => {
    if (await confirm('删除本地别名', `删除「${value}」？不会删除外部来源的同名别名。`)) await songCommit('delete', value);
  }));
  $('song-other-aliases').replaceChildren(aliases(selectedSong.other));
}
function openSong(item) {selectedSong = structuredClone(item); message('', false, 'song'); $('song-new-alias').value = ''; renderSongEditor(); $('song-dialog').showModal();}
async function songCommit(action, alias) {
  const result = await commit('manage/song-alias', {id: selectedSong.id, revision: selectedSong.revision, action, alias}, 'song');
  if (result) {
    try {
      const data = await bridge.apiGet('manage/songs', {q: selectedSong.id, page: 1});
      selectedSong = data.items.find(item => item.id === selectedSong.id) || selectedSong;
      $('song-new-alias').value = ''; renderSongEditor(); await load('songs');
    } catch (error) {message(error.message, false, 'song');}
  }
}
$('song-alias-form').addEventListener('submit', event => {event.preventDefault(); songCommit('add', $('song-new-alias').value.trim());});

if (!bridge) {
  for (const kind of kinds.filter(kind => kind !== 'resources')) $(`tab-${kind}`).disabled = true;
}
