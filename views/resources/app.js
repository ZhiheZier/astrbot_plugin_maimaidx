const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
let state = null;
let pending = false;
let fetching = false;
let timer = null;
let disposed = false;
let inventorySignature = '';
let sourcesSignature = '';
const phases = {idle: '暂无任务', uploading: '上传中', downloading: '下载中', extracting: '解压中', validating: '检查中', installing: '安装中', reloading: '加载中', rebuilding: '生成中', scanning: '检测中', completed: '已完成', failed: '失败', cancelled: '已取消'};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function iconButton(icon, title, handler) {
  const button = node('button', 'icon-button download-button');
  button.title = title;
  button.setAttribute('aria-label', title);
  const image = document.createElement('img');
  image.src = `./icons/${icon}.svg`;
  image.alt = '';
  button.append(image);
  button.addEventListener('click', handler);
  return button;
}

function notice(message = '') {
  $('notice').textContent = message;
  $('notice').hidden = !message;
}

function bytes(value) {
  if (!value) return '0 B';
  const exponent = Math.min(3, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** exponent).toFixed(exponent ? 1 : 0)} ${['B', 'KB', 'MB', 'GB'][exponent]}`;
}

async function confirmInstall(title) {
  const dialog = $('confirm-dialog');
  if (dialog.open) return false;
  $('confirm-title').textContent = `安装${title}`;
  $('confirm-message').textContent = '已有同名图片、字体将被替换。用户绑定、群配置及别名数据不会改动。';
  dialog.returnValue = 'cancel';
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once: true}));
}

function render(data) {
  state = data;
  const inventory = data.inventory || {groups: [], tables: []};
  const job = data.job || {phase: 'idle'};
  const busy = data.busy || pending;
  $('version').textContent = data.version ? `v${data.version}` : '—';
  $('resource-path').textContent = inventory.path || '—';
  $('ready-label').textContent = inventory.ready ? '资源就绪' : '待补齐';
  $('ready-label').dataset.state = inventory.ready ? 'ready' : 'missing';
  $('ready-title').textContent = inventory.ready ? '基础绘图资源检查通过' : '部分绘图功能暂不可用';
  const missing = (inventory.groups || []).reduce((total, group) => total + group.missing.length, 0);
  $('ready-count').textContent = missing ? `缺少或损坏 ${missing} 项` : `本地曲绘 ${inventory.cover_count || 0} 张`;
  const nextInventorySignature = JSON.stringify(inventory.groups);
  if (nextInventorySignature !== inventorySignature) {
  inventorySignature = nextInventorySignature;
  $('inventory').replaceChildren();
  for (const group of inventory.groups || []) {
    const row = node('div', 'inventory-row');
    const line = node('div', 'inventory-summary');
    const metadata = node('div', 'inventory-meta');
    metadata.append(node('span', 'count', `${group.present} / ${group.total}`), node('span', `state ${group.ready ? 'good' : 'warn'}`, group.ready ? '正常' : '待补齐'));
    line.append(node('span', 'inventory-name', group.name), metadata);
    row.append(line);
    if (group.missing.length) {
      const detail = document.createElement('details');
      detail.append(node('summary', '', `缺失或损坏项 · ${group.missing.length}`));
      const list = node('ul', 'missing-list');
      for (const path of group.missing) list.append(node('li', '', path));
      detail.append(list);
      row.append(detail);
    }
    $('inventory').append(row);
  }
  }
  const nextSourcesSignature = JSON.stringify(data.sources);
  if (nextSourcesSignature !== sourcesSignature) {
  sourcesSignature = nextSourcesSignature;
  $('packages').replaceChildren();
  for (const pack of data.sources || []) {
    const row = node('div', 'package-row');
    const button = iconButton('download', `下载并安装${pack.name}`, () => download(pack));
    button.disabled = busy;
    row.append(node('span', 'package-name', pack.name), node('span', 'package-version', pack.version), node('span', 'package-kind', pack.kind), button);
    $('packages').append(row);
  }
  }
  for (const button of $('packages').querySelectorAll('button')) button.disabled = busy;
  $('tables').replaceChildren();
  for (const table of inventory.tables || []) {
    const item = node('div', 'table-item');
    item.append(node('span', '', table.name), node('span', `state ${table.ready ? 'good' : 'warn'}`, table.ready ? `${table.count} 张` : '尚未生成'));
    $('tables').append(item);
  }
  $('rescan').disabled = busy;
  $('choose-file').disabled = busy;
  $('rebuild').disabled = busy || !inventory.ready;
  $('cancel').disabled = !job.cancellable;
  $('retry').hidden = !['failed', 'cancelled'].includes(job.phase) || !job.source_id;
  $('retry').disabled = busy;
  $('job-phase').textContent = phases[job.phase] || job.phase;
  $('job-details').hidden = !job.phase || job.phase === 'idle';
  $('task-idle').hidden = !$('job-details').hidden;
  $('cancel').hidden = !job.cancellable;
  $('job-phase').dataset.state = job.phase;
  $('job-title').textContent = job.title || '等待操作';
  $('job-message').textContent = job.message || '—';
  if (job.total > 0) {
    const percent = Math.min(100, Math.max(0, job.current / job.total * 100));
    $('progress').value = percent;
    $('progress-percent').textContent = `${Math.round(percent)}%`;
    $('progress-label').textContent = job.phase === 'downloading' ? `${bytes(job.current)} / ${bytes(job.total)}` : `${job.current} / ${job.total}`;
  } else {
    if (data.busy) $('progress').removeAttribute('value'); else $('progress').value = job.phase === 'completed' ? 100 : 0;
    $('progress-percent').textContent = '';
    $('progress-label').textContent = job.phase === 'uploading' ? bytes(job.current) : (data.busy ? '处理中' : '—');
  }
  $('digest-details').hidden = !job.sha256;
  $('digest').textContent = job.sha256 || '';
  $('events').replaceChildren();
  $('task-records').hidden = !job.events?.length;
  for (const event of [...(job.events || [])].reverse()) {
    const item = document.createElement('li');
    const time = new Date(event.at * 1000).toLocaleTimeString([], {hour12: false, hour: '2-digit', minute: '2-digit'});
    item.append(node('time', '', time), node('span', '', event.message));
    $('events').append(item);
  }
}

async function refresh() {
  if (fetching || !bridge || disposed) return;
  fetching = true;
  try {
    render(await bridge.apiGet('resources/status'));
  } catch (error) {
    notice(error.message || '无法读取资源状态。');
  } finally {
    fetching = false;
    clearTimeout(timer);
    if (!disposed) timer = setTimeout(refresh, state?.busy ? 1500 : 5000);
  }
}

async function perform(operation) {
  if (pending) return;
  pending = true;
  notice();
  if (state) render(state);
  try {
    const data = await operation();
    if (data?.inventory) render(data);
  } catch (error) {
    notice(error.message || '操作失败，请稍后重试。');
  } finally {
    pending = false;
    await refresh();
    if (state) render(state);
  }
}

async function download(pack) {
  if (state?.busy || pending || !await confirmInstall(pack.name)) return;
  await perform(() => bridge.apiPost('resources/download', {source_id: pack.id}));
}

async function upload(file) {
  if (!file || pending || state?.busy) return;
  if (!/\.(zip|7z)$/i.test(file.name)) return notice('请选择 ZIP 或 7z 格式资源包。');
  if (file.size > (state?.max_upload || 1024 ** 3)) return notice('上传资源包超过1GB限制，请使用在线下载或手动安装。');
  if (!await confirmInstall(file.name)) return;
  await perform(() => bridge.upload('resources/upload', file));
}

$('rescan').addEventListener('click', () => perform(() => bridge.apiPost('resources/rescan', {})));
$('rebuild').addEventListener('click', () => perform(() => bridge.apiPost('resources/rebuild', {})));
$('cancel').addEventListener('click', () => bridge && bridge.apiPost('resources/cancel', {}).then(refresh).catch(error => notice(error.message)));
$('retry').addEventListener('click', () => { const pack = state?.sources.find(pack => pack.id === state.job.source_id); if (pack) download(pack); });
$('choose-file').addEventListener('click', () => $('file-input').click());
$('file-input').addEventListener('change', () => { const file = $('file-input').files[0]; $('file-input').value = ''; upload(file); });
$('copy-path').addEventListener('click', async () => { try { await navigator.clipboard.writeText(state?.inventory.path || ''); } catch { notice('复制失败，请从资源目录栏选择文本复制。'); } });
for (const name of ['dragenter', 'dragover']) $('drop-zone').addEventListener(name, (event) => { event.preventDefault(); if (!state?.busy && !pending) $('drop-zone').classList.add('dragging'); });
for (const name of ['dragleave', 'drop']) $('drop-zone').addEventListener(name, (event) => { event.preventDefault(); $('drop-zone').classList.remove('dragging'); if (name === 'drop') upload(event.dataTransfer.files[0]); });
window.addEventListener('beforeunload', () => { disposed = true; clearTimeout(timer); });

if (!bridge) {
  notice('未连接 AstrBot 管理接口。');
  $('ready-label').textContent = '未连接';
  for (const id of ['rescan', 'choose-file', 'rebuild', 'copy-path']) $(id).disabled = true;
} else {
  try {
    const context = await bridge.ready();
    const theme = (context) => { document.documentElement.dataset.theme = context.isDark ? 'dark' : 'light'; };
    theme(context);
    bridge.onContext?.(theme);
    await refresh();
  } catch (error) { notice(error.message || '管理接口尚未就绪。'); }
}
