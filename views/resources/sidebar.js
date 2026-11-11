const toggle = document.getElementById('sidebar-toggle');
const sidebar = document.getElementById('task-sidebar');
const layout = document.getElementById('console-layout');

toggle.addEventListener('click', () => {
  const collapsed = !sidebar.hidden;
  sidebar.hidden = collapsed;
  layout.dataset.sidebarCollapsed = String(collapsed);
  toggle.setAttribute('aria-expanded', String(!collapsed));
  toggle.title = collapsed ? '展开侧栏' : '收起侧栏';
  toggle.setAttribute('aria-label', toggle.title);
  toggle.querySelector('img').src = `./icons/chevron-${collapsed ? 'left' : 'right'}.svg`;
  window.dispatchEvent(new CustomEvent('mai:sidebar', {detail: {collapsed}}));
});
