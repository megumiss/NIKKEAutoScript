# bin/parsec_vdd

NKAS 内置的 ParsecVDD 虚拟屏管理 CLI，用于在 `PCClient.VddType = parsecvdd` 时
创建 / 纠正 / 移除 Parsec 虚拟显示器。

## 来源

- 上游项目：[nomi-san/parsec-vdd](https://github.com/nomi-san/parsec-vdd)
- 分支 / 提交：`main` 分支 `a827c71`
- 许可：MIT
- 本目录内容为在上述提交上自行编译的 `ParsecDisplay`（CLI 模式即上游的 `vdd` 命令），
  未做源码改动。

## 文件

| 文件 | 说明 |
| --- | --- |
| `ParsecDisplay.exe` | 主程序。`-silent` 常驻托盘并恢复注册表中的屏幕；`-cli <cmd>` 走 CLI |
| `ParsecDisplay.exe.config` | .NET Framework 4.7.2 运行时绑定配置 |
| `vdd.cmd` | 上游附带的调用包装（`ParsecDisplay.exe -cli %*`），NKAS 不依赖 |
| `System.*.dll` | 上游随包发布的依赖程序集 |

上游的 `debug.log` 未收录（运行时日志，非必需）。

## 前置条件

**需用户自行安装官方 Parsec VDD 驱动**（Parsec Virtual Display Driver 的 exe 安装包）。
NKAS 只依赖「驱动已安装」，不依赖 ParsecDisplay 的安装路径。

驱动是否就绪可用本目录的 `ParsecDisplay.exe -cli version` 查看，正常输出形如：

```
Parsec Virtual Display Adapter
- Status: OK
- Version: 0.45
```

## 调用约定

```
ParsecDisplay.exe -cli list                 # 列出已添加的虚拟屏
ParsecDisplay.exe -cli add                  # 添加一块虚拟屏
ParsecDisplay.exe -cli remove all           # 移除全部虚拟屏
ParsecDisplay.exe -silent                   # 常驻运行（托盘），按注册表快照重建屏幕
```

注意：`-cli` 的退出码不可靠 —— 例如 `list` 在「有屏幕」时返回 1、「无屏幕」时返回 0。
调用方应解析 stdout，而不是判断退出码。输出文本使用系统 ANSI 代码页编码。

`vdd add` 默认产生 1920x1080@60 横屏；竖屏由 NKAS 侧用 `ChangeDisplaySettingsEx`
（`dmDisplayOrientation=1`，宽高互换为 1080x1920）设置，Parsec 驱动接受该模式。
