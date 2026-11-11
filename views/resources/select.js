const widgets = new Map();
let opened = null;

function enhance(select) {
  if (widgets.has(select) || select.multiple) return;
  const wrapper = document.createElement('span'); wrapper.className = 'custom-select';
  const button = document.createElement('button'); button.type = 'button'; button.className = 'select-toggle';
  const label = document.createElement('span');
  const arrow = document.createElement('img'); arrow.src = './icons/chevron-right.svg'; arrow.alt = '';
  button.append(label, arrow);
  const list = document.createElement('div'); list.className = 'select-menu'; list.hidden = true;
  list.id = `${select.id || `select-${widgets.size}`}-options`; list.setAttribute('role', 'listbox'); list.tabIndex = -1;
  button.setAttribute('role', 'combobox'); button.setAttribute('aria-haspopup', 'listbox');
  button.setAttribute('aria-controls', list.id); button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-label', select.getAttribute('aria-label') || select.closest('label')?.firstChild?.textContent.trim() || '选择');
  select.before(wrapper); wrapper.append(select, button, list);
  select.classList.add('select-native'); select.tabIndex = -1; select.setAttribute('aria-hidden', 'true');
  let current = 0;
  let signature = '';
  let typeahead = '', typedAt = 0;
  function position() {
    const box = button.getBoundingClientRect();
    list.style.left = `${Math.max(8, Math.min(box.left, innerWidth - Math.max(box.width, 130) - 8))}px`;
    list.style.width = `${Math.max(box.width, 130)}px`;
    list.style.maxHeight = `${Math.max(90, Math.min(240, innerHeight - box.bottom - 12))}px`;
    list.style.top = `${box.bottom + 5}px`;
    if (innerHeight - box.bottom < 110 && box.top > 150) {list.style.top = 'auto'; list.style.bottom = `${innerHeight - box.top + 5}px`;}
    else list.style.bottom = 'auto';
  }
  function refresh() {
    const next = `${select.value}:${select.disabled}:${[...select.options].map(item => item.textContent).join('|')}`;
    if (signature === next) return;
    signature = next; label.textContent = select.selectedOptions[0]?.textContent || '请选择'; button.disabled = select.disabled;
  }
  function close(focus = false) {
    list.hidden = true; button.setAttribute('aria-expanded', 'false');
    if (opened?.button === button) opened = null;
    if (focus) button.focus();
  }
  function highlight() {
    [...list.children].forEach((item, index) => item.dataset.active = String(index === current));
    const item = list.children[current];
    if (item) {list.setAttribute('aria-activedescendant', item.id); item.scrollIntoView({block: 'nearest'});}
  }
  function choose(index) {
    if (!select.options[index] || select.options[index].disabled) return;
    select.selectedIndex = index; refresh(); close(true); select.dispatchEvent(new Event('change', {bubbles: true}));
  }
  function open() {
    opened?.close(); refresh(); list.replaceChildren();
    [...select.options].forEach((option, index) => {
      const item = document.createElement('div'); item.id = `${list.id}-${index}`; item.textContent = option.textContent;
      item.setAttribute('role', 'option'); item.setAttribute('aria-selected', String(option.selected));
      item.setAttribute('aria-disabled', String(option.disabled));
      item.addEventListener('pointermove', () => {current = index; highlight();});
      item.addEventListener('click', () => choose(index)); list.append(item);
    });
    current = Math.max(0, select.selectedIndex); list.hidden = false; position(); highlight();
    button.setAttribute('aria-expanded', 'true'); opened = {button, close, wrapper, position}; list.focus();
  }
  button.addEventListener('click', () => list.hidden ? open() : close());
  wrapper.addEventListener('keydown', event => {
    if (event.key === 'Tab') return close();
    if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); return close(true);}
    if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) {
      event.preventDefault();
      if (list.hidden) return open();
      if (event.key === 'Enter' || event.key === ' ') return choose(current);
      if (event.key === 'Home') current = 0;
      else if (event.key === 'End') current = select.options.length - 1;
      else current = (current + (event.key === 'ArrowDown' ? 1 : -1) + select.options.length) % select.options.length;
      highlight();
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
      const now = Date.now(); typeahead = (now - typedAt > 700 ? '' : typeahead) + event.key.toLowerCase(); typedAt = now;
      if (list.hidden) open();
      const index = [...select.options].findIndex(option => option.textContent.toLowerCase().startsWith(typeahead));
      if (index >= 0) {current = index; highlight();}
    }
  });
  select.addEventListener('change', refresh);
  widgets.set(select, {refresh, close}); refresh();
}

export function enhanceSelects() {
  for (const [select, widget] of widgets) {if (!select.isConnected) {widget.close(); widgets.delete(select);} else widget.refresh();}
  document.querySelectorAll('select').forEach(enhance);
}
document.addEventListener('pointerdown', event => {if (opened && !opened.wrapper.contains(event.target)) opened.close();});
document.addEventListener('scroll', () => opened?.position(), true);
window.addEventListener('resize', () => opened?.position());
document.addEventListener('close', () => opened?.close(), true);
new MutationObserver(enhanceSelects).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['disabled']});
enhanceSelects();
