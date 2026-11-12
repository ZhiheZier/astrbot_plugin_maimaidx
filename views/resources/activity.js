const $ = id => document.getElementById(id);
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const statuses = {responded: '已响应', handled: '已处理', submitted: '已提交', error: '异常', cancelled: '中断', running: '进行中', completed: '已完成'};
const scopes = {chat: '功能', web: '管理', resource: '资源'};
let scope = 'all', page = 1, pages = 1, timer, generation = 0, closed = false;

function node(tag, className, text) {const item = document.createElement(tag); item.className = className; if (text !== undefined) item.textContent = text; return item;}
async function load() {
  clearTimeout(timer);
  if (!bridge || closed || document.hidden || $('task-sidebar').hidden) return;
  const request = ++generation;
  try {
    const data = await bridge.apiGet('manage/activity', {scope, page});
    if (request !== generation || closed) return;
    if (!Array.isArray(data.items)) throw new Error('日志接口尚未就绪，请重载插件。');
    page = data.page; pages = data.pages;
    $('activity-count').textContent = `${data.total} 条`; $('activity-page').textContent = `${page} / ${pages}`;
    $('activity-prev').disabled = page <= 1; $('activity-next').disabled = page >= pages;
    $('activity-error').hidden = data.persistent !== false;
    $('activity-error').textContent = data.persistent === false ? '日志暂存于内存，请检查持久化目录。' : '';
    $('activity-empty').hidden = data.items.length > 0; $('activity-events').replaceChildren();
    for (const row of data.items) {
      const item = node('li', '');
      const heading = node('div', 'activity-line');
      const date = new Date(row.at * 1000);
      const at = node('time', '', date.toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
      }));
      if (Number.isFinite(date.getTime())) at.dateTime = date.toISOString();
      at.title = at.textContent;
      const status = node('span', 'activity-status', statuses[row.status] || '—'); status.dataset.state = row.status;
      heading.append(at, node('span', 'activity-scope', scopes[row.scope] || ''), status);
      const detail = [row.actor ? `QQ ${row.actor}` : '', row.group ? `群 ${row.group}` : row.actor ? '私聊' : '', row.bot ? `BOT ${row.bot}` : '', row.duration_ms ? `${row.duration_ms} ms` : ''].filter(Boolean).join(' · ');
      item.append(heading, node('div', 'activity-label', row.label));
      if (detail) item.append(node('div', 'activity-detail', detail));
      $('activity-events').append(item);
    }
  } catch (error) {
    if (request === generation) {$('activity-error').hidden = false; $('activity-error').textContent = error.message || '日志暂不可用。';}
  } finally {
    if (!closed && request === generation) timer = setTimeout(load, 5000);
  }
}
for (const button of $('activity-scopes').children) button.addEventListener('click', () => {
  scope = button.dataset.scope; page = 1;
  for (const item of $('activity-scopes').children) item.setAttribute('aria-pressed', String(item === button));
  load();
});
$('activity-prev').addEventListener('click', () => {if (page > 1) {page--; load();}});
$('activity-next').addEventListener('click', () => {if (page < pages) {page++; load();}});
$('activity-refresh').addEventListener('click', load);
document.addEventListener('visibilitychange', () => {if (document.hidden) {clearTimeout(timer); generation++;} else load();});
window.addEventListener('mai:sidebar', event => {
  generation++; clearTimeout(timer);
  if (!event.detail.collapsed) load();
});
window.addEventListener('beforeunload', () => {closed = true; generation++; clearTimeout(timer);});
if (bridge) Promise.resolve(bridge.ready()).then(load).catch(() => {$('activity-error').hidden = false; $('activity-error').textContent = '日志连接失败，请刷新管理页。';});
else $('activity-refresh').disabled = true;
