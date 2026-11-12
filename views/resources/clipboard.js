export async function copyText(value) {
  if (typeof value !== 'string' || !value) throw new Error('没有可复制的内容。');
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
  } catch { /* HTTP pages and embedded views may deny the modern API. */ }
  const focus = document.activeElement;
  const selection = window.getSelection();
  const ranges = selection ? Array.from({length: selection.rangeCount}, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
  const inputSelection = focus && typeof focus.selectionStart === 'number' ? [focus.selectionStart, focus.selectionEnd] : null;
  const area = document.createElement('textarea');
  area.value = value;
  area.readOnly = true;
  area.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;pointer-events:none';
  // A modal dialog makes elements outside it inert.
  (document.querySelector('dialog[open]') || document.body).append(area);
  try {
    area.focus({preventScroll: true});
    area.select();
    area.setSelectionRange(0, value.length);
    if (!document.execCommand('copy')) throw new Error('浏览器禁止复制，请手动选择文本复制。');
  } finally {
    area.remove();
    if (focus?.isConnected) focus.focus({preventScroll: true});
    if (inputSelection) focus.setSelectionRange(...inputSelection);
    if (selection) {selection.removeAllRanges(); for (const range of ranges) selection.addRange(range);}
  }
}
