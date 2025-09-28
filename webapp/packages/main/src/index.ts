import { app, Menu, Tray, BrowserWindow, ipcMain, globalShortcut } from 'electron';
import { URL } from 'url';
import { PyShell } from '/@/pyshell';
import { webuiArgs, webuiPath, dpiScaling, webuiUrl } from '/@/config';

const path = require('path');

// === 全局快捷键常量定义 ===
const GLOBAL_SHORTCUTS = {
  START: 'F9',
  STOP: 'F10',
  RESTART: 'F11',
  DEV_TOOLS: 'Ctrl+Shift+I',
  REFRESH: 'Ctrl+R',
  HARD_REFRESH: 'Ctrl+Shift+R'
};

// 检查单实例锁
const isSingleInstance = app.requestSingleInstanceLock();

if (!isSingleInstance) {
  app.quit();
  process.exit(0);
}

app.disableHardwareAcceleration();

// 开发环境安装 Vue Devtools
if (import.meta.env.MODE === 'development') {
  app.whenReady()
    .then(() => import('electron-devtools-installer'))
    .then(({ default: installExtension, VUEJS3_DEVTOOLS }) => installExtension(VUEJS3_DEVTOOLS, {
      loadExtensionOptions: {
        allowFileAccess: true,
      },
    }))
    .catch(e => console.error('Failed install extension:', e));
}

// 启动 Python 服务
let nkas = new PyShell(webuiPath, webuiArgs);
nkas.end(function (err: string) {
  // if (err) throw err;
});

let mainWindow: BrowserWindow | null = null;

/**
 * 创建主窗口
 */
const createWindow = async () => {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 880,
    show: false,
    frame: false,
    icon: path.join(__dirname, './buildResources/icon.ico'),
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      nativeWindowOpen: true,
    },
  });

  // 窗口显示控制
  mainWindow.on('ready-to-show', () => {
    mainWindow?.show();
    Menu.setApplicationMenu(null);
    
    if (import.meta.env.MODE === 'development') {
      mainWindow?.webContents.openDevTools();
    }
  });

  // 窗口控制事件
  ipcMain.on('window-tray', () => mainWindow?.hide());
  ipcMain.on('window-min', () => mainWindow?.minimize());
  ipcMain.on('window-max', () => 
    mainWindow?.isMaximized() ? mainWindow?.restore() : mainWindow?.maximize()
  );
  ipcMain.on('window-close', () => 
    nkas.kill(() => mainWindow?.close())
  );

  // 托盘菜单
  const tray = new Tray(path.join(__dirname, 'icon.png'));
  const contextMenu = Menu.buildFromTemplate([
    { label: 'Show', click: () => mainWindow?.show() },
    { label: 'Hide', click: () => mainWindow?.hide() },
    { 
      label: 'Exit', 
      click: () => nkas.kill(() => mainWindow?.close()) 
    }
  ]);
  tray.setToolTip('NKAS');
  tray.setContextMenu(contextMenu);
  tray.on('click', () => mainWindow?.isVisible() ? mainWindow?.hide() : mainWindow?.show());
  tray.on('right-click', () => tray.popUpContextMenu(contextMenu));
};

// DPI 设置
if (!dpiScaling) {
  app.commandLine.appendSwitch('high-dpi-support', '1');
  app.commandLine.appendSwitch('force-device-scale-factor', '1');
}

/**
 * 加载应用 URL
 */
function loadURL() {
  const pageUrl = import.meta.env.MODE === 'development' && import.meta.env.VITE_DEV_SERVER_URL !== undefined
    ? import.meta.env.VITE_DEV_SERVER_URL
    : new URL('../renderer/dist/index.html', 'file://' + __dirname).toString();
  
  mainWindow?.loadURL(pageUrl);
}

// Python 服务启动检测
nkas.on('stderr', function (message: string) {
  if (message.includes('Application startup complete') || message.includes('bind on address')) {
    nkas.removeAllListeners('stderr');
    loadURL();
  }
});

// 处理第二个实例请求
app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    if (!mainWindow.isVisible()) mainWindow.show();
    mainWindow.focus();
  }
});

// 窗口关闭处理
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// 自定义 fetch 函数
function customFetch(url: string, options: any = {}) {
  return new Promise((resolve, reject) => {
    try {
      const { protocol, hostname, port, pathname, search } = new URL(url);
      const isHttps = protocol === 'https:';
      
      const httpModule = isHttps ? require('https') : require('http');
      
      const requestOptions = {
        hostname,
        port: port || (isHttps ? 443 : 80),
        path: pathname + search,
        method: options.method || 'GET',
        headers: options.headers || {}
      };
      
      const req = httpModule.request(requestOptions, (res: any) => {
        let data = '';
        
        res.on('data', (chunk: any) => {
          data += chunk;
        });
        
        res.on('end', () => {
          resolve({
            ok: res.statusCode >= 200 && res.statusCode < 300,
            status: res.statusCode,
            json: () => Promise.resolve(data ? JSON.parse(data) : {}),
            text: () => Promise.resolve(data)
          });
        });
      });
      
      req.on('error', (error: any) => {
        reject(error);
      });
      
      if (options.body) {
        req.write(options.body);
      }
      
      req.end();
    } catch (error) {
      reject(error);
    }
  });
}

// === 全局快捷键处理函数 ===
async function handleStart() {
  const response = await customFetch(`${webuiUrl}/api/all/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  });

  await (response as any).json();
}

async function handleStop() {
  const response = await customFetch(`${webuiUrl}/api/all/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  });
  
  await (response as any).json();
}

async function handleRestart() {
  const response = await customFetch(`${webuiUrl}/api/restart`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  });

  await (response as any).json();
}

// === 专用快捷键注册函数 ===
function registerGlobalShortcuts() {
  // 始终生效的全局快捷键
  const globalShortcuts = [
    { key: 'START', accelerator: GLOBAL_SHORTCUTS.START, handler: handleStart },
    { key: 'STOP', accelerator: GLOBAL_SHORTCUTS.STOP, handler: handleStop },
    { key: 'RESTART', accelerator: GLOBAL_SHORTCUTS.RESTART, handler: handleRestart }
  ];

  globalShortcuts.forEach(({ key, accelerator, handler }) => {
    // 确保不重复注册
    if (globalShortcut.isRegistered(accelerator)) {
      globalShortcut.unregister(accelerator);
    }
    
    const success = globalShortcut.register(accelerator, handler);
    if (!success) {
      console.error(`[GlobalShortcut] Failed to register ${accelerator} for ${key}`);
    } else {
      console.log(`[GlobalShortcut] Registered: ${accelerator} (${key})`);
    }
  });

  // 条件生效的快捷键（始终注册但条件执行）
  const conditionalShortcuts = [
    { 
      accelerator: GLOBAL_SHORTCUTS.DEV_TOOLS, 
      handler: () => {
        if (mainWindow?.isFocused()) {
          mainWindow.webContents.isDevToolsOpened() 
            ? mainWindow.webContents.closeDevTools()
            : mainWindow.webContents.openDevTools();
        }
      }
    },
    { 
      accelerator: GLOBAL_SHORTCUTS.REFRESH, 
      handler: () => {
        if (mainWindow?.isFocused()) mainWindow.reload();
      }
    },
    { 
      accelerator: GLOBAL_SHORTCUTS.HARD_REFRESH, 
      handler: () => {
        if (mainWindow?.isFocused()) mainWindow.reload();
      }
    }
  ];

  conditionalShortcuts.forEach(({ accelerator, handler }) => {
    if (globalShortcut.isRegistered(accelerator)) {
      globalShortcut.unregister(accelerator);
    }
    
    const success = globalShortcut.register(accelerator, handler);
    if (!success) {
      console.error(`[GlobalShortcut] Failed to register conditional shortcut: ${accelerator}`);
    }
  });
}

// === 应用初始化 ===
app.whenReady()
  .then(() => {
    createWindow();
    registerGlobalShortcuts(); // 统一注册所有快捷键
    
    // 开发环境检查更新
    if (import.meta.env.PROD) {
      import('electron-updater')
        .then(({ autoUpdater }) => autoUpdater.checkForUpdatesAndNotify())
        .catch(e => console.error('Failed check updates:', e));
    }
  })
  .catch(e => console.error('Failed create window:', e));

// === 资源清理 ===
app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  console.log('[GlobalShortcut] All shortcuts unregistered');
  nkas.kill(() => {});
});