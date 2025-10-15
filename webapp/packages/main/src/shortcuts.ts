import * as fs from "fs";
import * as path from "path";
import * as yaml from "yaml";
import { nkasPath } from '/@/config';

export function loadShortcuts() {
  const shortcutsPath = path.join(nkasPath, './config/shortcuts.yaml');

  // 默认快捷键（防止缺失字段）
  const defaultShortcuts = {
    UPDATE: 'F8',
    START: 'F9',
    STOP: 'F10',
    RESTART: 'F11',
    ROTATE: 'Ctrl+F12',
    DEV_TOOLS: 'Ctrl+Shift+I',
    REFRESH: 'Ctrl+R',
    HARD_REFRESH: 'Ctrl+Shift+R'
  };

  if (!fs.existsSync(shortcutsPath)) {
    console.warn('[Shortcuts] config/shortcuts.yaml not found, using defaults');
    return defaultShortcuts;
  }

  try {
    const file = fs.readFileSync(shortcutsPath, 'utf8');
    const userShortcuts = yaml.parse(file);
    return { ...defaultShortcuts, ...userShortcuts }; // 用户配置覆盖默认
  } catch (e) {
    console.error('[Shortcuts] Failed to parse shortcuts.yaml, using defaults:', e);
    return defaultShortcuts;
  }
}

// 导出
export const GLOBAL_SHORTCUTS = loadShortcuts();
