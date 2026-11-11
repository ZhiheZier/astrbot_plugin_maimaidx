function element(tag, className, text) {
  const item = document.createElement(tag);
  item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

let scheduled = false;
function scheduleLayout() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    for (const top of document.querySelectorAll('.pagination-top')) {
      const dialog = top.closest('dialog');
      if (top.parentElement.closest('[hidden]') || (dialog && !dialog.open) || !top.childElementCount) {
        top.hidden = true;
        continue;
      }
      const root = dialog || document.scrollingElement;
      // Measure without the duplicate, including layouts whose min-height absorbs its height.
      top.hidden = true;
      const overflow = root.scrollHeight > root.clientHeight + 1;
      top.hidden = !overflow;
    }
  });
}
new ResizeObserver(scheduleLayout).observe(document.body);
new MutationObserver(records => {
  if (records.some(record => record.type !== 'attributes' || !record.target.classList.contains('pagination-top'))) scheduleLayout();
}).observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'open']});
window.addEventListener('resize', scheduleLayout);
document.fonts?.ready.then(scheduleLayout);

export function renderPagination(target, data, navigate, {disabled = false, label = '列表', unit = '条'} = {}) {
  const current = Math.max(1, Number(data.page) || 1);
  const last = Math.max(1, Number(data.pages) || 1);
  target.classList.add('pagination');
  target.setAttribute('aria-label', `${label}分页`);
  const summary = element('span', 'page-summary', `共 ${data.total ?? 0} ${unit} · ${current} / ${last} 页`);
  const controls = element('div', 'page-controls');
  function button(title, number, icon, text) {
    const item = element('button', icon ? 'icon-button page-step' : 'page-number', text);
    item.type = 'button'; item.title = title; item.setAttribute('aria-label', title);
    item.disabled = disabled || number < 1 || number > last || number === current;
    if (icon) {
      const img = element('img', ''); img.src = `./icons/${icon}.svg`; img.alt = '';
      item.append(img);
    } else if (number === current) item.setAttribute('aria-current', 'page');
    item.addEventListener('click', () => navigate(number));
    return item;
  }
  controls.append(button('上一页', current - 1, 'chevron-left'), button('首页', 1, null, '1'));
  const start = Math.max(2, Math.min(current - 1, last - 3));
  const end = Math.min(last - 1, start + 2);
  if (start > 2) controls.append(element('span', 'page-ellipsis', '…'));
  for (let number = start; number <= end; number++) controls.append(button(`第 ${number} 页`, number, null, String(number)));
  if (end < last - 1) controls.append(element('span', 'page-ellipsis', '…'));
  if (last > 1) controls.append(button('末页', last, null, String(last)));
  controls.append(button('下一页', current + 1, 'chevron-right'));
  const jump = element('form', 'page-jump');
  const inputLabel = element('label', '', '跳至');
  const input = element('input', '');
  input.type = 'number'; input.min = '1'; input.max = String(last); input.step = '1';
  input.value = String(current); input.required = true; input.inputMode = 'numeric';
  input.setAttribute('aria-label', `${label}跳转页码`); input.disabled = disabled || last <= 1;
  const submit = element('button', 'icon-button'); submit.type = 'submit'; submit.title = '跳转'; submit.setAttribute('aria-label', '跳转');
  const arrow = element('img', ''); arrow.src = './icons/chevron-right.svg'; arrow.alt = ''; submit.append(arrow);
  submit.disabled = input.disabled;
  inputLabel.append(input, document.createTextNode('页')); jump.append(inputLabel, submit);
  jump.addEventListener('submit', event => {
    event.preventDefault();
    if (!jump.reportValidity() || disabled) return;
    const number = Number(input.value);
    if (Number.isInteger(number) && number >= 1 && number <= last && number !== current) navigate(number);
  });
  target.replaceChildren(summary, controls, jump);
  scheduleLayout();
}
