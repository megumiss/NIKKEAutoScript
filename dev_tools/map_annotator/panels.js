'use strict';

function showManagerPanel(name) {
  for (const tab of document.querySelectorAll('[data-panel]')) {
    const selected = tab.dataset.panel === name;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    document.getElementById(`panel-${tab.dataset.panel}`).hidden = !selected;
  }
}

for (const tab of document.querySelectorAll('[data-panel]')) {
  tab.addEventListener('click', () => showManagerPanel(tab.dataset.panel));
  tab.addEventListener('keydown', event => {
    const tabs = [...document.querySelectorAll('[data-panel]')];
    let index = tabs.indexOf(tab);
    if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') index = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = tabs.length - 1;
    else return;
    event.preventDefault(); showManagerPanel(tabs[index].dataset.panel); tabs[index].focus();
  });
}
