const timers = new WeakMap();

export function showNotice(item, text = '', ok = false) {
  clearTimeout(timers.get(item));
  timers.delete(item);
  item.textContent = text;
  item.hidden = !text;
  item.dataset.ok = String(ok);
  if (text && ok) timers.set(item, setTimeout(() => {
    item.hidden = true;
    item.textContent = '';
    timers.delete(item);
  }, 3000));
}
