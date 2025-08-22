import {app, BrowserWindow, ipcMain} from 'electron'
import {nkas} from "../main";

ipcMain.handle('WindowToTray', async (e) => {
    //主窗口ID
    BrowserWindow.fromId(1)?.hide()
})

ipcMain.handle('WindowToMin', async (e) => {
    //主窗口ID
    BrowserWindow.fromId(1)?.minimize()
})

ipcMain.handle('WindowToFullScreen', async (e) => {
    let win = BrowserWindow.fromId(1)
    win?.setFullScreen(!win.isFullScreen())

})
ipcMain.handle('WindowToClose', async (e) => {
    nkas.kill(() => {
        app.quit()
    })
})
