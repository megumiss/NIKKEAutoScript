// @ts-ignore
import path from 'path'
import {app, BrowserWindow, Tray, Menu, nativeImage, ipcMain} from 'electron'
import {PyShell} from '../src/config/pyshell';
import {webuiArgs} from '../src/config/config';
import './ipc/WindowStrategy'

export const nkas = new PyShell('gui.py', webuiArgs);

nkas.end(function (err: string) {
    console.log(err)
});

let tray: Tray | null = null; // 托盘对象
let win: BrowserWindow | null = null;
let isQuiting = false; // ← 用局部变量，不污染 app

setTimeout(() => {
    const WinState = require('electron-win-state').default

    const winState = new WinState({
        defaultWidth: 1280,
        defaultHeight: 720
    })

    function createWindow() {
        // 创建浏览器窗口
        win = new BrowserWindow({
            ...winState.winOptions,
            show: false,
            icon: path.join(__dirname, '../../dist/Helm.ico'),
            //边框
            frame: false,
            titleBarStyle: 'hiddenInset',
            webPreferences: {
                preload: path.join(__dirname, 'preload.js'),
                //在渲染进程使用node
                nodeIntegration: true,
                contextIsolation: false,
                //跨域
                webSecurity: false
            }
        })
        if (app.isPackaged) {
            win.loadFile(path.join(__dirname, '../../dist/index.html'))
            // win.webContents.openDevTools()
        } else {
            win.loadURL('http://localhost:5173/')
            win.webContents.openDevTools()
        }
        win.on('ready-to-show', () => {
            win?.show();
        })
        winState.manage(win)
    }

    function createTray() {
        const iconPath = path.join(__dirname, '../../dist/Helm.ico') // 使用现有图标
        const trayIcon = nativeImage.createFromPath(iconPath);
        tray = new Tray(trayIcon);

        const contextMenu = Menu.buildFromTemplate([
            {
                label: '显示窗口',
                click: () => {
                    win?.show();
                    win?.focus();
                }
            },
            {
                label: '退出',
                click: () => {
                    isQuiting = true; // ← 退出时设置 isQuiting = true
                    app.quit();
                }
            }
        ]);
        tray.setToolTip('NKAS');
        tray.setContextMenu(contextMenu);

        tray.on('click', () => {
            if (win?.isVisible()) {
                win.hide();
            } else {
                win?.show();
            }
        });
    }

    app.whenReady().then(() => {
        createWindow();
        createTray();
    });

    app.on('window-all-closed', () => {
        // 不退出应用，保持托盘运行
    });

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });

    ipcMain.on('minimize-to-tray', () => {
        win?.hide();
    });

}, 0)
