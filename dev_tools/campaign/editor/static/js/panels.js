'use strict';

/** 切换右侧管理页签，保留各页参数与任务状态，只改变可见区域和键盘焦点顺序。 */
function showManagerPanel(name) {
  for (const tab of document.querySelectorAll('[data-panel]')) {
    const selected = tab.dataset.panel === name;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    document.getElementById(`panel-${tab.dataset.panel}`).hidden = !selected;
  }
}

/** 切换左侧编辑任务；工具快捷键调用时同步页签，不重置绘制参数或道路修订。 */
function showEditorPanel(name) {
  for (const tab of document.querySelectorAll('[data-editor-panel]')) {
    const selected = tab.dataset.editorPanel === name;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    document.getElementById(`${tab.dataset.editorPanel}-tools`).hidden = !selected;
  }
}

/** 为单个页签组绑定鼠标与方向键操作，不让左右侧栏的焦点导航相互影响。
 * 左侧切换前先退出当前工具；未完成的绘制由编辑器阻止切换，保留草稿和当前页签。
 */
function bindPanelTabs(selector, activate) {
  const tabs = [...document.querySelectorAll(selector)];
  for (const tab of tabs) {
    tab.addEventListener('click', () => activate(tab));
    tab.addEventListener('keydown', event => {
      let index = tabs.indexOf(tab);
      if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') index = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = tabs.length - 1;
      else return;
      event.preventDefault();
      activate(tabs[index]);
      if (tabs[index].getAttribute('aria-selected') === 'true') tabs[index].focus();
    });
  }
}

bindPanelTabs('[data-panel]', tab => showManagerPanel(tab.dataset.panel));
bindPanelTabs('[data-editor-panel]', tab => {
  document.querySelector('[data-tool="select"]').click();
  if (document.getElementById('canvas').dataset.tool !== 'select') return;
  showEditorPanel(tab.dataset.editorPanel);
});

/** 文件浮层不占侧栏布局；点击外部或按 Escape 后归还属性区的交互空间。 */
const fileDetails = document.querySelector('.file-details');
document.addEventListener('click', event => {
  if (fileDetails.open && !fileDetails.contains(event.target)) fileDetails.open = false;
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || !fileDetails.open) return;
  fileDetails.open = false;
  fileDetails.querySelector('summary').focus();
});
