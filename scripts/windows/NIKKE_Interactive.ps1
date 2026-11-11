<#
.SYNOPSIS
    NIKKE insightjs IOC 扫描与恶意组件处理的交互式入口。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$reportRoot = Join-Path $PSScriptRoot 'scan-reports'

function Invoke-NikkeScan {
    [CmdletBinding()]
    param([switch]$Full, [string]$OutDir)

$ErrorActionPreference = 'Continue'
$ProgressPreference   = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

if (-not $OutDir -or $OutDir.Trim() -eq '') {
    if ($PSScriptRoot) { $OutDir = $PSScriptRoot } else { $OutDir = (Get-Location).Path }
}

$script:Findings  = New-Object System.Collections.Generic.List[object]
$script:StartTime = Get-Date
$script:LastMark  = Get-Date
$script:IsAdmin   = $false
try {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object System.Security.Principal.WindowsPrincipal($id)
    $script:IsAdmin = $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
} catch {}

# ============================ IOC 定义 ============================
# 来源：公开分析报告 + Sheisback/nikke_malware_scanner v1.3.0（该工具已人工审计：只读、
# 无外连、不删文件）中对外的 IOC 常量。所有判定均为字符串/哈希比对，不执行任何可疑内容。
$Ioc = @{
    DllSha256    = '931747D56B9639829AECD5540991D53DF336FF4E93B30C7DB0C8DF7A6038942E'
    BundleSha256 = 'CC0AD05BFE85940A6067B3A72C182D3953339129206634141926D5D0B35973EC'
    DllName      = 'platform_experience_helper.dll'
    AuxDllNames  = @('service_util.dll')
    MarkerFiles  = @('old-clsid.txt')
    Clsid        = '{E77CC89B-7401-4C04-8CED-149DB35ADD04}'
    Domains      = @('insightjs.com', 'analytics.insightjs.com', 'insightjs.org')
    Ips          = @('89.127.233.127')
    AgentUuid    = '8d1f80ac-57f4-4c61-8935-3f935abcaaaa'
    AgentPath    = '/px/8d1f80ac57f44c6189353f935abcaaaa'
}

# 日志特征：执行级（= 恶意 Lua 已在 Launcher 内跑起来）与下载级（= 拉到了恶意 bundle）
$LogExecPatterns    = @('k2f_chan', '8d1f80ac-57f4-4c61-8935-3f935abcaaaa', 'agent started as')
$LogDownloadPatterns= @('analytics.insightjs.com', 'insight.bundle', 'lua downloaded to')
$LogDownloadSuccess = @('curl perform success', 'result=0', 'result:0', 'total=205135', 'size=205135', 'lua downloaded to')
# Srv_DownloadFile 是 NIKKE 正常也会用的下载桥，不能单独当恶意证据，仅作上下文

# ---------------------------------------------------------------- 输出工具

function Write-Head([string]$t) {
    $now = Get-Date
    $dur = ($now - $script:LastMark).TotalSeconds
    $script:LastMark = $now
    Write-Host ''
    Write-Host ('-' * 70) -ForegroundColor DarkCyan
    Write-Host ('  ' + $t) -ForegroundColor Cyan
    if ($dur -ge 2) { Write-Host ('  (上一阶段耗时 ' + [math]::Round($dur, 1) + ' 秒)') -ForegroundColor DarkGray }
    Write-Host ('-' * 70) -ForegroundColor DarkCyan
}

function Trim-Text([string]$s, [int]$max) {
    if (-not $s) { return '' }
    $s = $s.Trim()
    if ($s.Length -le $max) { return $s }
    return $s.Substring(0, $max) + ' ... (已截断)'
}

function Add-Finding {
    param(
        [string]$Level,
        [string]$Category,
        [string]$Title,
        [string]$Evidence
    )
    if (-not $Evidence) { $Evidence = '' }
    [void]$script:Findings.Add([pscustomobject]@{
        Level    = $Level
        Category = $Category
        Title    = $Title
        Evidence = $Evidence
    })

    $tagMap   = @{ HIT = '[!! 命中 ]'; WARN = '[? 待确认]'; INFO = '[i 信息 ]'; CLEAN = '[v 正常 ]' }
    $colorMap = @{ HIT = 'Red'; WARN = 'Yellow'; INFO = 'Gray'; CLEAN = 'Green' }
    Write-Host ('{0} [{1}] {2}' -f $tagMap[$Level], $Category, $Title) -ForegroundColor $colorMap[$Level]
    if ($Evidence) {
        foreach ($line in ($Evidence -split "`r?`n")) {
            if ($line.Trim().Length -gt 0) {
                Write-Host ('           ' + $line) -ForegroundColor DarkGray
            }
        }
    }
}

# 判定：区分「Chrome 官方同名组件」与「恶意 IOC」
#   恶意特征 = 路径落在用户可写目录（AppData/Temp/ProgramData/Users），或无签名
#   官方特征 = 路径在 Program Files\Google\Chrome\Application 下且签名有效（Google）
function Get-PehJudgement {
    param([string]$CommandLine)
    if (-not $CommandLine) { return 'NONE' }
    if ($CommandLine -match 'insightjs|k2f_chan') { return 'HIT' }
    if ($CommandLine -notmatch 'platform_experience_helper') { return 'NONE' }

    $p = $CommandLine.Trim()
    if ($p.StartsWith('"')) {
        $p = ($p -split '"')[1]
    } elseif (-not (Test-Path -LiteralPath $p)) {
        $p = ($p -split '\s+')[0]   # 无引号且整串不是有效路径时，取第一个 token
    }
    if (-not $p) { return 'HIT' }

    $inProgramFiles = $false
    foreach ($pf in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($pf -and $p.ToLower().StartsWith(($pf + '\Google\Chrome\Application\').ToLower())) { $inProgramFiles = $true }
    }
    if (-not $inProgramFiles) { return 'HIT' }
    if (-not (Test-Path -LiteralPath $p)) { return 'HIT' }
    $s = Get-AuthenticodeSignature -LiteralPath $p -ErrorAction SilentlyContinue
    if ($s -and $s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'Google') { return 'OFFICIAL' }
    return 'HIT'
}

# ---------------------------------------------------------------- 底层读取

function Test-BinaryContains {
    param(
        [string]$Path,
        [string]$Needle,
        [long]$MaxBytes = 400MB,
        [int]$Chunk = 4194304
    )
    try {
        $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    } catch { return $false }
    try {
        if ($fs.Length -gt $MaxBytes) { return $false }
        $nLen = $Needle.Length
        $prev = ''
        $buf  = [byte[]]::new($Chunk)
        while ($true) {
            $read = $fs.Read($buf, 0, $Chunk)
            if ($read -le 0) { break }
            $s = $prev + [System.Text.Encoding]::ASCII.GetString($buf, 0, $read)
            if ($s.IndexOf($Needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
            if ($nLen -gt 1 -and $s.Length -ge ($nLen - 1)) {
                $prev = $s.Substring($s.Length - ($nLen - 1))
            } else {
                $prev = ''
            }
        }
        return $false
    } catch {
        return $false
    } finally {
        try { $fs.Dispose() } catch {}
    }
}

function Test-IsScannerArtifact {
    param([System.IO.FileInfo]$File)
    $selfPath = $script:PSCommandPath
    if ($selfPath -and [string]::Equals($File.FullName, $selfPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    if ($File.Name -like 'nikke_ioc_scan_*.txt') { return $true }
    return $File.FullName -match '[\\/](?:scan-reports|quarantine-[^\\/]+)[\\/]'
}

function Get-TextFileHits {
    param([string]$Dir, [string[]]$Extensions, [string]$Pattern, [int]$MaxSizeMB = 8, [int]$Depth = 4)
    $out = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Dir)) { return $out }
    $maxBytes = $MaxSizeMB * 1MB
    $files = @()
    try {
        $files = Get-ChildItem -LiteralPath $Dir -Recurse -File -Depth $Depth -ErrorAction SilentlyContinue |
                 Where-Object {
                     $Extensions -contains $_.Extension.ToLower() -and $_.Length -gt 0 -and $_.Length -lt $maxBytes -and
                     $_.Name -ne 'debug.log' -and -not (Test-IsScannerArtifact -File $_)
                 }
    } catch {}
    foreach ($f in $files) {
        if (Test-BinaryContains -Path $f.FullName -Needle $Pattern -MaxBytes $maxBytes) {
            $lines = @()
            try {
                $lines = Select-String -LiteralPath $f.FullName -Pattern $Pattern -ErrorAction SilentlyContinue |
                         Select-Object -First 5 |
                         ForEach-Object { 'L' + $_.LineNumber + ': ' + (Trim-Text $_.Line 160) }
            } catch {}
            [void]$out.Add([pscustomobject]@{ File = $f.FullName; Lines = ($lines -join "`n") })
        }
    }
    return $out
}

# ---------------------------------------------------------------- 1. Launcher 目录

function Get-NikkeCandidateDirs {
    $found = New-Object System.Collections.Generic.HashSet[string]

    # 1) 注册表卸载项
    $uk = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($k in $uk) {
        try {
            Get-ItemProperty -Path $k -ErrorAction SilentlyContinue | ForEach-Object {
                $dn = $_.DisplayName
                if ($dn -and $dn -match 'NIKKE|Goddess of Victory') {
                    Add-Finding -Level 'INFO' -Category 'Launcher' -Title ('卸载项记录：' + $dn) `
                        -Evidence ('InstallLocation = ' + $_.InstallLocation)
                    foreach ($cand in @($_.InstallLocation, $_.DisplayIcon, $_.UninstallString)) {
                        if (-not $cand) { continue }
                        $p = [string]$cand
                        $p = $p -replace '^"', ''
                        $p = $p -replace '".*$', ''
                        $p = $p -replace ',\s*-?\d+$', ''
                        $p = $p.Trim().Trim('"')
                        if ($p -and (Test-Path -LiteralPath $p)) {
                            $item = Get-Item -LiteralPath $p -ErrorAction SilentlyContinue
                            if ($item -and -not $item.PSIsContainer) { $p = Split-Path -Parent $p }
                            [void]$found.Add($p)
                        }
                    }
                }
            }
        } catch {}
    }

    # 2) 常见固定路径 + 各盘根一层目录
    $bases = New-Object System.Collections.Generic.List[string]
    foreach ($r in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA, $env:APPDATA,
                     "$env:USERPROFILE\AppData\LocalLow", $env:USERPROFILE, 'C:\', 'D:\', 'E:\', 'F:\', 'G:\', 'H:\')) {
        if ($r -and (Test-Path -LiteralPath $r)) { [void]$bases.Add($r) }
    }
    foreach ($b in $bases) {
        try {
            Get-ChildItem -LiteralPath $b -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match 'NIKKE|Goddess of Victory' } |
                ForEach-Object { [void]$found.Add($_.FullName) }
        } catch {}
    }

    # 3) 开始菜单快捷方式
    $sm = @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs", "$env:APPDATA\Microsoft\Windows\Start Menu\Programs")
    $wsh = $null
    try { $wsh = New-Object -ComObject WScript.Shell } catch {}
    if ($wsh) {
        foreach ($s in $sm) {
            if (-not (Test-Path -LiteralPath $s)) { continue }
            try {
                Get-ChildItem -LiteralPath $s -Recurse -Filter '*.lnk' -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match 'NIKKE|Goddess' } |
                    ForEach-Object {
                        try {
                            $t = $wsh.CreateShortcut($_.FullName).TargetPath
                            if ($t -and (Test-Path -LiteralPath $t)) {
                                [void]$found.Add((Split-Path -Parent $t))
                            }
                        } catch {}
                    }
            } catch {}
        }
    }

    return @($found)
}

function Test-NikkeInstallDir {
    param([string]$Dir)
    if (-not $Dir -or -not (Test-Path -LiteralPath $Dir)) { return $false }
    if (Test-Path -LiteralPath (Join-Path $Dir 'Launcher.exe')) { return $true }
    if (Test-Path -LiteralPath (Join-Path $Dir 'Launcher'))    { return $true }
    try {
        $hit = Get-ChildItem -LiteralPath $Dir -Directory -Filter 'intl_service' -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $true }
    } catch {}
    return $false
}

Write-Head '1/6  NIKKE Launcher 目录 + debug.log（核心确认项）'
$nikkeDirs = @(Get-NikkeCandidateDirs)
$IocWindowStart = [datetime]'2026-09-27'   # insightjs.com 被重新注册的日期

if ($Full) {
    Write-Host '  [Full 模式] 全盘搜索 intl_service 目录，耗时可达数分钟 ...' -ForegroundColor Yellow
    try {
        Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
            Where-Object { $_.Root -match '^[A-Za-z]:\\$' } |
            ForEach-Object {
                try {
                    Get-ChildItem -LiteralPath $_.Root -Recurse -Directory -Filter 'intl_service' -ErrorAction SilentlyContinue |
                        ForEach-Object { $nikkeDirs += $_.Parent.FullName }
                } catch {}
            }
    } catch {}
    $nikkeDirs = @($nikkeDirs | Select-Object -Unique)
}

$nikkeLogs     = @()
$nikkeSvc      = @()
$nikkeRealDirs = @()

if ($nikkeDirs.Count -eq 0) {
    Add-Finding -Level 'WARN' -Category 'Launcher' -Title '未定位到 NIKKE Launcher 安装目录' `
        -Evidence '若你从未玩过 PC 版 NIKKE，此项可忽略；若玩过，请加 -Full 参数重扫，或手动确认 Launcher\intl_service\debug.log 所在路径'
} else {
    Add-Finding -Level 'INFO' -Category 'Launcher' -Title ('定位到 ' + $nikkeDirs.Count + ' 个名称匹配目录') -Evidence ($nikkeDirs -join "`n")

    foreach ($d in $nikkeDirs) {
        if (Test-NikkeInstallDir -Dir $d) { $nikkeRealDirs += $d }
    }
    if ($nikkeRealDirs.Count -eq 0) { $nikkeRealDirs = $nikkeDirs }
    if ($nikkeRealDirs.Count -lt $nikkeDirs.Count) {
        Add-Finding -Level 'INFO' -Category 'Launcher' -Title ('其中 ' + $nikkeRealDirs.Count + ' 个确认为安装目录，剩余为同名素材目录已跳过') `
            -Evidence ($nikkeRealDirs -join "`n")
    }

    foreach ($d in $nikkeRealDirs) {
        try { $nikkeLogs += Get-ChildItem -LiteralPath $d -Recurse -File -Filter 'debug.log' -ErrorAction SilentlyContinue } catch {}
        try { $nikkeSvc  += Get-ChildItem -LiteralPath $d -Recurse -Directory -Filter 'intl_service' -ErrorAction SilentlyContinue } catch {}
        # TinyGet 下载日志（恶意 bundle 通常从这里落地）
        try {
            $tiny = Get-ChildItem -LiteralPath $d -Recurse -Directory -Filter 'tinyget' -Depth 4 -ErrorAction SilentlyContinue
            foreach ($t in $tiny) {
                $nikkeLogs += Get-ChildItem -LiteralPath $t.FullName -File -Filter '*.log' -ErrorAction SilentlyContinue
            }
        } catch {}
    }
    $nikkeLogs = @($nikkeLogs | Sort-Object FullName -Unique)
    $nikkeSvc  = @($nikkeSvc  | Sort-Object FullName -Unique)
    if ($nikkeSvc.Count -gt 0) {
        Add-Finding -Level 'INFO' -Category 'Launcher' -Title ('找到 intl_service 目录 ' + $nikkeSvc.Count + ' 个') `
            -Evidence (($nikkeSvc | Select-Object -Unique -Property FullName | ForEach-Object { $_.FullName }) -join "`n")
    }

    if ($nikkeLogs.Count -eq 0) {
        Add-Finding -Level 'WARN' -Category 'Launcher' -Title '未找到 debug.log' `
            -Evidence 'Launcher 可能装在非默认路径。请加 -Full 重扫，或手动搜索 intl_service\debug.log'
    } else {
        # 分级判定：执行级 > 下载成功 > 仅请求 > 干净
        $execLines   = @()
        $dlOkLines   = @()
        $dlTryLines  = @()
        foreach ($lg in $nikkeLogs) {
            $ms = @()
            try { $ms = @(Select-String -LiteralPath $lg.FullName -Pattern $LogExecPatterns -SimpleMatch -ErrorAction SilentlyContinue) } catch {}
            foreach ($m in $ms) {
                $execLines += ('[' + $lg.Name + '] L' + $m.LineNumber + '  ' + (Trim-Text $m.Line.Trim() 220))
            }
            $md = @()
            try { $md = @(Select-String -LiteralPath $lg.FullName -Pattern $LogDownloadPatterns -SimpleMatch -ErrorAction SilentlyContinue) } catch {}
            foreach ($m in $md) {
                $lineTxt = '[' + $lg.Name + '] L' + $m.LineNumber + '  ' + (Trim-Text $m.Line.Trim() 220)
                $ok = $false
                foreach ($s in $LogDownloadSuccess) {
                    if ($m.Line -imatch [regex]::Escape($s)) { $ok = $true; break }
                }
                if ($ok) { $dlOkLines += $lineTxt } else { $dlTryLines += $lineTxt }
            }
        }

        if ($execLines.Count -gt 0) {
            Add-Finding -Level 'HIT' -Category 'Launcher' -Title 'Launcher 日志确认恶意 Lua 已执行（k2f_chan / agent started / 会话 UUID）' `
                -Evidence ("执行级证据 " + $execLines.Count + " 行（恶意代码已在 Launcher 内运行，等价 3~4 级）`n" + (Trim-Text (($execLines | Select-Object -First 20) -join "`n") 3000))
            if ($dlOkLines.Count -gt 0) {
                Add-Finding -Level 'HIT' -Category 'Launcher' -Title '同时存在恶意 bundle 下载成功记录' -Evidence (Trim-Text (($dlOkLines | Select-Object -First 10) -join "`n") 1500)
            }
        } elseif ($dlOkLines.Count -gt 0) {
            Add-Finding -Level 'HIT' -Category 'Launcher' -Title '恶意 insight.bundle 已下载成功，但未见 Lua 执行记录（等价 2 级）' `
                -Evidence ("下载成功证据 " + $dlOkLines.Count + " 行`n" + (Trim-Text (($dlOkLines | Select-Object -First 12) -join "`n") 2500) + "`n注意：下载成功不等于已执行，但该 Launcher 已被投毒，务必停用")
        } elseif ($dlTryLines.Count -gt 0) {
            Add-Finding -Level 'WARN' -Category 'Launcher' -Title '日志仅见恶意域名/文件名请求，下载成功与执行均未确认（等价 1 级）' `
                -Evidence (Trim-Text (($dlTryLines | Select-Object -First 12) -join "`n") 2000)
        } else {
            $listEv = (($nikkeLogs | Sort-Object LastWriteTime -Descending | Select-Object -First 15 |
                       ForEach-Object { $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm') + '  ' + $_.Length + 'B  ' + $_.FullName }) -join "`n")
            $listEv += "`n（共 " + $nikkeLogs.Count + " 个日志，此处仅列最近 15 个；完整清单见报告文件）"
            Add-Finding -Level 'CLEAN' -Category 'Launcher' -Title ('已检查 ' + $nikkeLogs.Count + ' 个日志（debug.log + tinyget），无执行/下载级 IOC') `
                -Evidence $listEv
        }

        # 时间窗口对照：Launcher 是否在恶意活动期内启动过。
        # 注意：仅"在窗口内启动过"不等于感染，只能算"可能暴露"，降级为 INFO，不能触发已失陷。
        $inWindow = @($nikkeLogs | Where-Object { $_.LastWriteTime -ge $IocWindowStart })
        if ($inWindow.Count -gt 0) {
            $ev = ($inWindow | ForEach-Object { $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm') + '  ' + $_.FullName }) -join "`n"
            Add-Finding -Level 'INFO' -Category 'Launcher' -Title ('Launcher 在恶意活动窗口内（' + $IocWindowStart.ToString('yyyy-MM-dd') + ' 之后）启动过') `
                -Evidence ($ev + "`n这只是时间线索，不代表已感染。请结合上面的执行级/下载级命中综合判断，勿单独据此处置")
        } else {
            $latest = ($nikkeLogs | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
            Add-Finding -Level 'CLEAN' -Category 'Launcher' -Title ('所有 Launcher 日志最后写入均早于 ' + $IocWindowStart.ToString('yyyy-MM-dd') + '（域名抢注日），未落入风险窗口') `
                -Evidence ('最近一次启动：' + $latest.LastWriteTime.ToString('yyyy-MM-dd HH:mm') + '  ' + $latest.FullName)
        }
    }
}

# ---------------------------------------------------------------- 2. k2f_chan 文本扩散

Write-Head '2/6  Launcher 目录内 k2f_chan 文本扩散 + 近期可疑脚本'
if ($nikkeRealDirs.Count -gt 0) {
    $exts = @('.js', '.mjs', '.json', '.log', '.txt', '.html', '.htm', '.xml', '.ini', '.cfg', '.lua', '.luac', '.bat', '.cmd', '.ps1', '.py', '.vbs')

    # 恶意 JS 只可能由 Launcher 加载：有 Launcher 子目录就只扫它，避免遍历游戏资源目录
    $searchRoots = @()
    foreach ($d in $nikkeRealDirs) {
        $sub = Join-Path $d 'Launcher'
        if (Test-Path -LiteralPath $sub -PathType Container) { $searchRoots += $sub } else { $searchRoots += $d }
    }
    $searchRoots = @($searchRoots | Select-Object -Unique)

    $khits = @()
    foreach ($d in $searchRoots) {
        $khits += @(Get-TextFileHits -Dir $d -Extensions $exts -Pattern 'k2f_chan')
    }
    if ($khits.Count -gt 0) {
        $ev = ($khits | Select-Object -First 15 | ForEach-Object { $_.File + "`n  " + ($_.Lines -replace "`n", "`n  ") }) -join "`n"
        Add-Finding -Level 'HIT' -Category 'Launcher' -Title ('除 debug.log 外，另有 ' + $khits.Count + ' 个文件含 k2f_chan') -Evidence (Trim-Text $ev 3000)
    } else {
        Add-Finding -Level 'CLEAN' -Category 'Launcher' -Title 'Launcher 目录内未发现其它 k2f_chan 文本痕迹'
    }

    # 近期改动 / 可疑脚本
    $recent = @()
    foreach ($d in $searchRoots) {
        try {
            $recent += Get-ChildItem -LiteralPath $d -Recurse -File -Depth 5 -ErrorAction SilentlyContinue |
                       Where-Object { ($exts -contains $_.Extension.ToLower()) -and $_.LastWriteTime -gt (Get-Date).AddDays(-60) -and $_.Length -lt 5MB }
        } catch {}
    }
    if ($recent.Count -gt 0) {
        $ev = ($recent | Sort-Object LastWriteTime -Descending | Select-Object -First 20 |
               ForEach-Object { $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm') + '  ' + $_.Length + 'B  ' + $_.FullName }) -join "`n"
        Add-Finding -Level 'WARN' -Category 'Launcher' -Title ('近 60 天内改动过的脚本/配置 ' + $recent.Count + ' 个，请人工核对是否为官方更新') -Evidence (Trim-Text $ev 3000)
    }

    # Lua 运行时 / 字节码
    # 说明：NIKKE Launcher 本身自带 Lua51.dll（官方运行时，属正常）；
    #       IOC 关注的是「外部下发的 .luac 字节码 / 脚本」，以及 Lua 运行时被替换。
    $luaDll  = @()
    $luaCode = @()
    foreach ($d in $searchRoots) {
        try {
            $all = Get-ChildItem -LiteralPath $d -Recurse -File -Depth 5 -ErrorAction SilentlyContinue
            $luaDll  += @($all | Where-Object { $_.Name -match '^(lua51|luajit|lua5|liblua)' -and $_.Extension -eq '.dll' })
            $luaCode += @($all | Where-Object { $_.Extension -match '^\.(luac|lua)$' })
        } catch {}
    }
    if ($luaCode.Count -gt 0) {
        Add-Finding -Level 'WARN' -Category 'Launcher' -Title ('存在 Lua 脚本/字节码文件 ' + $luaCode.Count + ' 个，官方客户端通常不落地明文脚本，需人工核对来源') `
            -Evidence (($luaCode | Select-Object -First 20 | ForEach-Object { $_.LastWriteTime.ToString('yyyy-MM-dd') + '  ' + $_.FullName }) -join "`n")
    } else {
        Add-Finding -Level 'CLEAN' -Category 'Launcher' -Title '未发现落地到磁盘的 .lua / .luac 脚本'
    }
    if ($luaDll.Count -gt 0) {
        $ev = ($luaDll | ForEach-Object {
            $sig = '未签名'
            try { $s = Get-AuthenticodeSignature -LiteralPath $_.FullName -ErrorAction SilentlyContinue; if ($s -and $s.Status -eq 'Valid') { $sig = '签名有效' } } catch {}
            $_.LastWriteTime.ToString('yyyy-MM-dd') + '  ' + $_.Length + 'B  ' + $_.FullName + '  [' + $sig + ']'
        }) -join "`n"
        Add-Finding -Level 'INFO' -Category 'Launcher' -Title ('出现 Lua 运行时 DLL ' + $luaDll.Count + ' 个（Launcher 官方自带，属正常；若时间戳在 2026-09 之后需留意是否被替换）') `
            -Evidence $ev
    }
} else {
    Add-Finding -Level 'INFO' -Category 'Launcher' -Title '跳过：未定位 Launcher 目录'
}

# ---------------------------------------------------------------- 3. Chromium 寄生 DLL

Write-Head '3/6  寄生 DLL / 文件残留（User Data、伪装路径、Launcher、TEMP）'
$browserRoots = @(
    [pscustomobject]@{ Name = 'Chrome';      Path = "$env:LOCALAPPDATA\Google\Chrome\User Data" },
    [pscustomobject]@{ Name = 'Chrome Beta'; Path = "$env:LOCALAPPDATA\Google\Chrome Beta\User Data" },
    [pscustomobject]@{ Name = 'Chrome Dev';  Path = "$env:LOCALAPPDATA\Google\Chrome Dev\User Data" },
    [pscustomobject]@{ Name = 'Edge';        Path = "$env:LOCALAPPDATA\Microsoft\Edge\User Data" },
    [pscustomobject]@{ Name = 'Brave';       Path = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data" },
    [pscustomobject]@{ Name = 'Vivaldi';     Path = "$env:LOCALAPPDATA\Vivaldi\User Data" },
    [pscustomobject]@{ Name = 'Opera';       Path = "$env:APPDATA\Opera Software\Opera Stable" },
    [pscustomobject]@{ Name = 'Opera GX';    Path = "$env:APPDATA\Opera Software\Opera GX Stable" }
)

$pehDirs    = @()
$pehRoots   = New-Object System.Collections.Generic.List[object]   # 用于后面历史库检查
$presentCnt = 0

$knownHash = @{}
$knownHash[$Ioc.DllSha256.ToUpper()]    = '已知恶意 DLL（platform_experience_helper.dll）'
$knownHash['3507AFE81982D19795CAD7280072A42A5A8F5AD5ECE22C48DC5627B7E9CCBF4B'] = '已知恶意 DLL（本机已逆向确认变体）'
$knownHash['4BFAF98A6B863FA79554D9EEEB18870C3F92D552D62F4BD35A72D19EA68526AF'] = '已知恶意 DLL（VirusTotal 公开样本）'
$knownHash[$Ioc.BundleSha256.ToUpper()] = '已知恶意 bundle（insight.bundle）'

function Get-FileVerdict {
    param([string]$Path)
    $sha = ''
    try { $sha = (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToUpper() } catch {}
    $sigTxt = '未签名'
    $signed = $false
    try {
        $s = Get-AuthenticodeSignature -LiteralPath $Path -ErrorAction SilentlyContinue
        if ($s -and $s.Status -eq 'Valid') { $signed = $true; $sigTxt = '签名有效：' + $s.SignerCertificate.Subject }
    } catch {}
    $lv = 'WARN'
    $why = '同名/同路径，但哈希不在已知列表中 —— 需人工确认'
    if ($sha -and $knownHash.ContainsKey($sha)) { $lv = 'HIT'; $why = $knownHash[$sha] }
    elseif ($signed) { $lv = 'INFO'; $why = '有有效数字签名' }
    return [pscustomobject]@{ Level = $lv; Reason = $why; Sha = $sha; Sig = $sigTxt }
}

# 3.1 Chromium 系 User Data 下的 PlatformExperienceHelper
foreach ($br in $browserRoots) {
    if (-not (Test-Path -LiteralPath $br.Path)) { continue }
    $presentCnt++
    [void]$pehRoots.Add($br)

    $tops = New-Object System.Collections.Generic.List[string]
    $p1 = Join-Path $br.Path 'PlatformExperienceHelper'
    if (Test-Path -LiteralPath $p1) { [void]$tops.Add($p1) }
    try {
        Get-ChildItem -LiteralPath $br.Path -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $p2 = Join-Path $_.FullName 'PlatformExperienceHelper'
            if (Test-Path -LiteralPath $p2) { [void]$tops.Add($p2) }
        }
    } catch {}

    foreach ($t in $tops) {
        $dlls = @()
        try { $dlls = Get-ChildItem -LiteralPath $t -Recurse -File -Filter '*.dll' -ErrorAction SilentlyContinue } catch {}
        if ($dlls.Count -eq 0) {
            Add-Finding -Level 'WARN' -Category 'Browser' -Title ('发现空目录（可疑残留）：' + $t) `
                -Evidence ($br.Name + '：目录存在但内部无 DLL，仍需确认是否曾被加载')
            $pehDirs += $t
        } else {
            $evLines = @()
            $anyHit  = $false
            foreach ($dll in $dlls) {
                $v = Get-FileVerdict -Path $dll.FullName
                if ($v.Level -eq 'HIT') { $anyHit = $true }
                $evLines += ($dll.FullName + "`n  判定: " + $v.Reason + "`n  SHA256: " + $v.Sha + "`n  写入时间: " + $dll.LastWriteTime + "`n  签名: " + $v.Sig)
                $pehDirs += $dll.FullName
            }
            $lv = 'WARN'
            if ($anyHit) { $lv = 'HIT' }
            Add-Finding -Level $lv -Category 'Browser' -Title ($br.Name + ' 存在 PlatformExperienceHelper 寄生 DLL ' + $dlls.Count + ' 个') `
                -Evidence (Trim-Text ($evLines -join "`n") 4000)
        }
    }
}

# 3.2 其它落点：用户目录伪装成 Application 路径、其它用户 profile、Launcher 目录内部
$extraRoots = @()
$extraRoots += (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\PlatformExperienceHelper')
try {
    Get-ChildItem -LiteralPath ($env:SystemDrive + '\Users') -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $p = Join-Path $_.FullName 'AppData\Local\Google\Chrome\User Data\PlatformExperienceHelper'
        if (Test-Path -LiteralPath $p) { $extraRoots += $p }
    }
} catch {}
foreach ($d in $nikkeRealDirs) { $extraRoots += $d }

$extraFiles = @()
$nameList = @($Ioc.DllName) + $Ioc.AuxDllNames + $Ioc.MarkerFiles
foreach ($r in ($extraRoots | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $r -PathType Container)) { continue }
    foreach ($n in $nameList) {
        try { $extraFiles += Get-ChildItem -LiteralPath $r -Recurse -File -Filter $n -Depth 4 -ErrorAction SilentlyContinue } catch {}
    }
}
$extraFiles = @($extraFiles | Sort-Object FullName -Unique)
if ($extraFiles.Count -gt 0) {
    $hits2 = @()
    $warns2 = @()
    foreach ($f in $extraFiles) {
        $v = Get-FileVerdict -Path $f.FullName
        $line = $f.FullName + "`n  判定: " + $v.Reason + "`n  SHA256: " + $v.Sha + "`n  时间: " + $f.LastWriteTime
        if ($v.Level -eq 'HIT') { $hits2 += $line } else { $warns2 += $line }
    }
    if ($hits2.Count -gt 0) {
        Add-Finding -Level 'HIT' -Category 'Filesystem' -Title ('发现 ' + $hits2.Count + ' 个哈希匹配的已知恶意文件') -Evidence (Trim-Text ($hits2 -join "`n") 4000)
    }
    if ($warns2.Count -gt 0) {
        Add-Finding -Level 'WARN' -Category 'Filesystem' -Title ('发现 ' + $warns2.Count + ' 个同名/同路径但哈希未知的文件（可能是新变种或旧残留）') -Evidence (Trim-Text ($warns2 -join "`n") 3000)
    }
} else {
    Add-Finding -Level 'CLEAN' -Category 'Filesystem' -Title '未发现 platform_experience_helper.dll / service_util.dll / old-clsid.txt 残留' `
        -Evidence (($extraRoots | Select-Object -Unique | ForEach-Object { '已查: ' + $_ }) -join "`n")
}

# 3.3 临时目录残留（凭据窃取与 bundle 下载会落地临时文件）
# k2f-bcreds-* 与 *insight.bundle* 是已验证强 IOC；泛化 k2f-* 存在碰撞误报，降为 WARN
$tmpHits = @()      # 强 IOC：k2f-bcreds-* / *insight.bundle*
$tmpWeak = @()      # 弱 IOC：其它 k2f-*
if ($env:TEMP -and (Test-Path -LiteralPath $env:TEMP)) {
    foreach ($pat in @('k2f-bcreds-*', '*insight.bundle*')) {
        try { $tmpHits += Get-ChildItem -LiteralPath $env:TEMP -Filter $pat -File -ErrorAction SilentlyContinue } catch {}
    }
    try { $tmpWeak += Get-ChildItem -LiteralPath $env:TEMP -Filter 'k2f-*' -File -ErrorAction SilentlyContinue } catch {}
}
$tmpHits = @($tmpHits | Sort-Object FullName -Unique)
$tmpWeak = @($tmpWeak | Sort-Object FullName -Unique)
if ($tmpHits.Count -gt 0) {
    $ev = ($tmpHits | ForEach-Object { $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm') + '  ' + $_.Length + 'B  ' + $_.FullName }) -join "`n"
    Add-Finding -Level 'HIT' -Category 'Temp' -Title ('临时目录存在 k2f-bcreds-* / insight.bundle 残留 ' + $tmpHits.Count + ' 个（说明凭据可能已被打包）') `
        -Evidence (Trim-Text $ev 2500)
} elseif ($tmpWeak.Count -gt 0) {
    $ev = ($tmpWeak | ForEach-Object { $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm') + '  ' + $_.Length + 'B  ' + $_.FullName }) -join "`n"
    Add-Finding -Level 'WARN' -Category 'Temp' -Title ('临时目录存在其它 k2f-* 文件 ' + $tmpWeak.Count + ' 个（非 k2f-bcreds，可能是新变种，也可能是无关文件，需人工核对）') `
        -Evidence (Trim-Text $ev 2500)
} else {
    Add-Finding -Level 'CLEAN' -Category 'Temp' -Title ('临时目录无 k2f-* / insight.bundle 残留（已查 ' + $env:TEMP + '）')
}

if ($pehDirs.Count -eq 0) {
    Add-Finding -Level 'CLEAN' -Category 'Browser' -Title ('已检查 ' + $presentCnt + ' 个浏览器数据目录，未发现 PlatformExperienceHelper') `
        -Evidence (($pehRoots | ForEach-Object { $_.Name + ' -> ' + $_.Path }) -join "`n")
}

# ---------------------------------------------------------------- 4. 注册表

Write-Head '4/6  注册表 CLSID InprocServer32 劫持'
$knownGuid = $Ioc.Clsid
$guidHives = @(
    'HKCU:\Software\Classes\CLSID',
    'HKLM:\SOFTWARE\Classes\CLSID',
    'HKLM:\SOFTWARE\Classes\WOW6432Node\CLSID'
)
$guidHit = 0
foreach ($h in $guidHives) {
    $kp = $h + '\' + $knownGuid
    if (Test-Path -LiteralPath $kp) {
        $guidHit++
        $dllVal = ''
        try { $dllVal = [string](Get-ItemProperty -LiteralPath ($kp + '\InprocServer32') -ErrorAction SilentlyContinue).'(default)' } catch {}
        Add-Finding -Level 'HIT' -Category 'Registry' -Title ('命中已知恶意 CLSID：' + $kp) -Evidence ('InprocServer32 = ' + $dllVal)
    }
}
if ($guidHit -eq 0) {
    Add-Finding -Level 'CLEAN' -Category 'Registry' -Title ('未发现 CLSID ' + $knownGuid)
}

# 变种扫描：任何 CLSID 的 InprocServer32 / LocalServer32 指向 platform_experience_helper
# 用 .NET 注册表 API 直接遍历，不依赖 reg.exe（reg.exe 可能被安全软件/策略禁用）
$variant   = @()
$officialCom = @()
$scopes = @(
    @{ Hive = [Microsoft.Win32.RegistryHive]::CurrentUser;    Sub = 'Software\Classes\CLSID';                    Label = 'HKCU' },
    @{ Hive = [Microsoft.Win32.RegistryHive]::LocalMachine;   Sub = 'SOFTWARE\Classes\CLSID';                    Label = 'HKLM' },
    @{ Hive = [Microsoft.Win32.RegistryHive]::LocalMachine;   Sub = 'SOFTWARE\Classes\WOW6432Node\CLSID';        Label = 'HKLM-WOW64' }
)
foreach ($sc in $scopes) {
    $base = $null
    try { $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($sc.Hive, [Microsoft.Win32.RegistryView]::Default) } catch {}
    if (-not $base) { continue }
    $ck = $null
    try { $ck = $base.OpenSubKey($sc.Sub) } catch {}
    if ($ck) {
        foreach ($name in $ck.GetSubKeyNames()) {
            foreach ($srv in @('InprocServer32', 'LocalServer32')) {
                $k2 = $null
                try { $k2 = $ck.OpenSubKey($name + '\' + $srv) } catch {}
                if (-not $k2) { continue }
                $v = ''
                try { $v = [string]$k2.GetValue('') } catch {}
                try { $k2.Close() } catch {}
                if ($v -match 'platform_experience_helper') {
                    $vd = Get-PehJudgement -CommandLine $v
                    $fullKey = $sc.Label + '\' + $sc.Sub + '\' + $name + '\' + $srv
                    if ($vd -eq 'HIT') {
                        $variant += ($fullKey + '  =  ' + $v)
                    } elseif ($vd -eq 'OFFICIAL') {
                        $officialCom += ($fullKey + '  =  ' + $v)
                    }
                }
            }
        }
        try { $ck.Close() } catch {}
    }
    try { $base.Close() } catch {}
}
if ($variant.Count -gt 0) {
    Add-Finding -Level 'HIT' -Category 'Registry' -Title ('发现 ' + $variant.Count + ' 个指向 platform_experience_helper 的 COM 劫持项') `
        -Evidence (Trim-Text (($variant | Select-Object -Unique) -join "`n") 2500)
} else {
    Add-Finding -Level 'CLEAN' -Category 'Registry' -Title '未发现指向 platform_experience_helper 的 COM 劫持（InprocServer32/LocalServer32）'
}
if ($officialCom.Count -gt 0) {
    Add-Finding -Level 'INFO' -Category 'Registry' -Title ('其中 ' + $officialCom.Count + ' 项指向 Program Files 下已签名的官方 exe，判为正常') `
        -Evidence (Trim-Text (($officialCom | Select-Object -Unique) -join "`n") 1500)
}

# ---------------------------------------------------------------- 5. insightjs.com 痕迹

Write-Head '5/6  insightjs.com 痕迹（hosts / DNS 缓存 / 浏览器历史 / 活动连接）'
$domainHit = 0

# 5.1 hosts
$hostsPath = 'C:\Windows\System32\drivers\etc\hosts'
try {
    $hh = Select-String -LiteralPath $hostsPath -Pattern 'insightjs' -ErrorAction SilentlyContinue
    if ($hh) {
        $domainHit++
        Add-Finding -Level 'WARN' -Category 'Network' -Title 'hosts 文件包含 insightjs' `
            -Evidence (($hh | ForEach-Object { 'L' + $_.LineNumber + ': ' + $_.Line }) -join "`n" + "`n注意：若这是你自己添加的封锁条目（如 0.0.0.0 insightjs.com），属正常防御，不代表感染")
    } else {
        Add-Finding -Level 'CLEAN' -Category 'Network' -Title 'hosts 文件无 insightjs 记录'
    }
} catch {
    Add-Finding -Level 'WARN' -Category 'Network' -Title 'hosts 文件读取失败（可能需管理员）'
}

# 5.2 DNS 缓存
$dnsIps = @()
try {
    $dc = Get-DnsClientCache -ErrorAction Stop | Where-Object { $_.Entry -match 'insightjs' }
    foreach ($e in $dc) {
        $dnsIps += $e.Data
        Add-Finding -Level 'WARN' -Category 'Network' -Title ('DNS 缓存中存在 insightjs 解析记录：' + $e.Entry) -Evidence ('记录值 = ' + $e.Data + "`n注意：手工查询过该域名或访问过安全分析文章也可能留下解析记录，需结合其它证据判断")
    }
    if (-not $dc) { Add-Finding -Level 'CLEAN' -Category 'Network' -Title 'DNS 缓存中无 insightjs 记录' }
} catch {
    try {
        $out = & ipconfig /displaydns 2>$null | Select-String -Pattern 'insightjs' -SimpleMatch
        if ($out) {
            Add-Finding -Level 'WARN' -Category 'Network' -Title 'DNS 缓存中存在 insightjs 记录' -Evidence (Trim-Text (($out | Select-Object -First 6 | ForEach-Object { $_.Line }) -join "`n") 1200)
        } else {
            Add-Finding -Level 'CLEAN' -Category 'Network' -Title 'DNS 缓存中无 insightjs 记录（ipconfig 回退模式）'
        }
    } catch {
        Add-Finding -Level 'WARN' -Category 'Network' -Title 'DNS 缓存检查失败'
    }
}

# 5.3 浏览器历史
$histFiles = @()
foreach ($br in $pehRoots) {
    $h1 = Join-Path $br.Path 'History'
    if (Test-Path -LiteralPath $h1) { $histFiles += $h1 }
    try {
        Get-ChildItem -LiteralPath $br.Path -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $h2 = Join-Path $_.FullName 'History'
            if (Test-Path -LiteralPath $h2) { $histFiles += $h2 }
        }
    } catch {}
}
foreach ($fp in @("$env:APPDATA\Mozilla\Firefox\Profiles")) {
    if (Test-Path -LiteralPath $fp) {
        try {
            Get-ChildItem -LiteralPath $fp -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                $p = Join-Path $_.FullName 'places.sqlite'
                if (Test-Path -LiteralPath $p) { $histFiles += $p }
            }
        } catch {}
    }
}

$histHits = @()
foreach ($hf in ($histFiles | Select-Object -Unique)) {
    if (Test-BinaryContains -Path $hf -Needle 'insightjs') { $histHits += $hf }
}
if ($histHits.Count -gt 0) {
    $domainHit++
    Add-Finding -Level 'WARN' -Category 'Network' -Title ('浏览器历史记录中出现 insightjs（' + $histHits.Count + ' 个库）') `
        -Evidence (($histHits) -join "`n" + "`n注意：自己访问过关于该域名的分析文章、或手工查询过它，也会留下历史记录，不能单独证明恶意程序访问过")
} else {
    Add-Finding -Level 'CLEAN' -Category 'Network' -Title ('已检查 ' + ($histFiles | Select-Object -Unique).Count + ' 个浏览器历史库，未出现 insightjs')
}

# 5.4 进程内已加载模块（强证据，但需区分官方 exe 与可疑 dll）
$modHit = 0
foreach ($pn in @('chrome', 'msedge', 'brave', 'vivaldi', 'opera')) {
    $ps = @()
    try { $ps = Get-Process -Name $pn -ErrorAction SilentlyContinue } catch {}
    foreach ($p in $ps) {
        try {
            $bad = $p.Modules | Where-Object { $_.FileName -match 'platform_experience_helper|PlatformExperienceHelper' }
            foreach ($b in $bad) {
                $modHit++
                if ($b.FileName -match '\.dll$') {
                    Add-Finding -Level 'HIT' -Category 'Process' -Title ('进程 ' + $p.ProcessName + ' (PID ' + $p.Id + ') 加载了可疑 DLL') -Evidence $b.FileName
                } else {
                    Add-Finding -Level 'INFO' -Category 'Process' -Title ('进程 ' + $p.ProcessName + ' (PID ' + $p.Id + ') 加载了 platform_experience_helper（非 DLL，可能为官方 exe 组件，请核对路径与签名）') -Evidence $b.FileName
                }
            }
        } catch {}
    }
}
if ($modHit -eq 0) {
    Add-Finding -Level 'CLEAN' -Category 'Process' -Title '当前运行中的浏览器进程未加载 platform_experience_helper'
}

# 5.5 活动外连
try {
    $conns = Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue |
             Where-Object { $_.RemoteAddress -notmatch '^(127\.|::1|0\.0\.0\.0|::)' }
    $procMap = @{}
    foreach ($c in $conns) {
        if (-not $procMap.ContainsKey($c.OwningProcess)) {
            $procMap[$c.OwningProcess] = ''
            try { $procMap[$c.OwningProcess] = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName } catch {}
        }
    }
    $susp = @()
    $badIp = @()
    foreach ($c in $conns) {
        $nm = $procMap[$c.OwningProcess]
        if ($nm -match '^(chrome|msedge|brave|vivaldi|opera)$') {
            $susp += ($nm + ' PID ' + $c.OwningProcess + ' -> ' + $c.RemoteAddress + ':' + $c.RemotePort)
        }
        foreach ($ip in $Ioc.Ips) {
            if ($c.RemoteAddress -eq $ip) {
                $badIp += ($nm + ' PID ' + $c.OwningProcess + ' -> ' + $c.RemoteAddress + ':' + $c.RemotePort)
            }
        }
    }
    if ($badIp.Count -gt 0) {
        Add-Finding -Level 'HIT' -Category 'Network' -Title ('检测到与已知恶意 IP 的活动连接（' + ($Ioc.Ips -join ', ') + '）') `
            -Evidence (($badIp | Select-Object -Unique) -join "`n")
    } elseif ($susp.Count -gt 0) {
        Add-Finding -Level 'INFO' -Category 'Network' -Title ('浏览器当前外连 ' + $susp.Count + ' 条，可人工核对是否有陌生 IP') `
            -Evidence (Trim-Text (($susp | Select-Object -First 25) -join "`n") 2500)
    } else {
        Add-Finding -Level 'CLEAN' -Category 'Network' -Title '浏览器当前无活动的对外连接'
    }
} catch {
    Add-Finding -Level 'WARN' -Category 'Network' -Title '活动连接枚举失败'
}

# ---------------------------------------------------------------- 6. 持久化

Write-Head '6/6  持久化点：Run 键 / 计划任务'
$persistHit  = 0
$officialCnt = 0

$runKeys = @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'
)
$runTotal = 0
foreach ($rk in $runKeys) {
    try {
        $props = Get-ItemProperty -LiteralPath $rk -ErrorAction SilentlyContinue
        if (-not $props) { continue }
        $props.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' } | ForEach-Object {
            $runTotal++
            $val = [string]$_.Value
            $verdict = Get-PehJudgement -CommandLine $val
            if ($verdict -eq 'HIT') {
                $persistHit++
                Add-Finding -Level 'HIT' -Category 'Persist' -Title ('Run 键可疑项：' + $rk + ' :: ' + $_.Name) -Evidence $val
            } elseif ($verdict -eq 'OFFICIAL') {
                $officialCnt++
            }
        }
    } catch {}
}
if ($persistHit -eq 0) {
    Add-Finding -Level 'CLEAN' -Category 'Persist' -Title ('Run/RunOnce 共 ' + $runTotal + ' 项，无 IOC 引用')
}

$taskHit = 0
$taskTotal = 0
$officialTasks = @()
try {
    Get-ScheduledTask -ErrorAction Stop | ForEach-Object {
        $taskTotal++
        $tname = $_.TaskName
        $tpath = $_.TaskPath
        foreach ($a in $_.Actions) {
            $cmd = [string]$a.Execute
            $txt = $cmd + ' ' + ([string]$a.Arguments)
            $verdict = Get-PehJudgement -CommandLine $txt
            if ($verdict -eq 'HIT') {
                $taskHit++
                Add-Finding -Level 'HIT' -Category 'Persist' -Title ('计划任务可疑项：' + $tpath + $tname) -Evidence (Trim-Text $txt.Trim() 600)
            } elseif ($verdict -eq 'OFFICIAL') {
                $officialCnt++
                $officialTasks += ($tpath + $tname + '  ->  ' + $cmd)
            }
        }
    }
} catch {
    Add-Finding -Level 'WARN' -Category 'Persist' -Title '计划任务枚举失败（可能需管理员）'
}
if ($taskHit -eq 0 -and $taskTotal -gt 0) {
    Add-Finding -Level 'CLEAN' -Category 'Persist' -Title ('已检查 ' + $taskTotal + ' 个计划任务，无恶意 IOC 引用')
}
if ($officialTasks.Count -gt 0) {
    Add-Finding -Level 'INFO' -Category 'Persist' -Title ('其中 ' + $officialTasks.Count + ' 项为 Chrome 官方同名组件（Program Files + 有效 Google 签名），不计入风险') `
        -Evidence ((($officialTasks | Select-Object -Unique) -join "`n") + "`n说明：官方 exe 名为 platform_experience_helper.exe；恶意样本是同名 DLL 放在 User Data 下，用路径与签名区分")
}

# ---------------------------------------------------------------- 汇总 + 报告

$hits  = @($script:Findings | Where-Object { $_.Level -eq 'HIT' })
$warns = @($script:Findings | Where-Object { $_.Level -eq 'WARN' })
$cleans= @($script:Findings | Where-Object { $_.Level -eq 'CLEAN' })
$infos = @($script:Findings | Where-Object { $_.Level -eq 'INFO' })

$verdict = '未发现已知 IOC 痕迹'
if ($hits.Count -gt 0) { $verdict = '发现 ' + $hits.Count + ' 项强证据命中（已知恶意哈希 / 恶意 CLSID / 日志确认执行 / 活动外连）—— 建议按已失陷处置' }
elseif ($warns.Count -gt 0) { $verdict = '无强证据命中，但有 ' + $warns.Count + ' 项待人工确认（弱证据 / 可疑痕迹，不代表已感染）' }

$report = New-Object System.Collections.Generic.List[string]
[void]$report.Add('================================================================')
[void]$report.Add(' NIKKE Launcher 恶意组件 IOC 自查报告（只读扫描）')
[void]$report.Add('================================================================')
[void]$report.Add(' 生成时间   : ' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))
[void]$report.Add(' 耗时       : ' + [math]::Round(((Get-Date) - $script:StartTime).TotalSeconds, 1) + ' 秒')
[void]$report.Add(' 计算机名   : ' + $env:COMPUTERNAME)
[void]$report.Add(' 用户名     : ' + $env:USERNAME)
[void]$report.Add(' 管理员权限 : ' + $(if ($script:IsAdmin) { '是' } else { '否（部分项可能不完整，建议以管理员重跑）' }))
[void]$report.Add(' 系统版本   : ' + (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Version)
[void]$report.Add(' 结论       : ' + $verdict)
[void]$report.Add(' IOC 基线   : 公开报告 + Sheisback/nikke_malware_scanner v1.3.0（源码已人工审计，只读）')
[void]$report.Add(' 统计       : 命中 ' + $hits.Count + ' / 待确认 ' + $warns.Count + ' / 信息 ' + $infos.Count + ' / 正常 ' + $cleans.Count)
[void]$report.Add('')
foreach ($f in $script:Findings) {
    [void]$report.Add('[' + $f.Level + '] [' + $f.Category + '] ' + $f.Title)
    if ($f.Evidence) {
        foreach ($l in ($f.Evidence -split "`r?`n")) {
            if ($l.Trim()) { [void]$report.Add('        ' + $l) }
        }
    }
    [void]$report.Add('')
}
[void]$report.Add('================================================================')
if ($hits.Count -gt 0) {
    [void]$report.Add(' 处置建议（脚本不会自动执行任何改动）')
    [void]$report.Add('================================================================')
    [void]$report.Add(' 1. 账号：浏览器中保存的全部密码视为已泄露，逐条更换；所有已登录站点强制退出后重登')
    [void]$report.Add(' 2. 开发/运维：轮换 SSH 私钥、服务器口令；吊销并重发 Git/GitHub/GitLab/npm/PyPI/')
    [void]$report.Add('    AWS/Anthropic/OpenAI/Google/Stripe/Slack/Vault 令牌；JWT 签名密钥一并轮换')
    [void]$report.Add(' 3. 数字钱包：助记词和私钥已泄露，必须新建钱包并转移资产，旧钱包永久废弃')
    [void]$report.Add(' 4. 游戏：韩国官方修复前不要再启动 PC 版 NIKKE')
    [void]$report.Add(' 5. 清理顺序：退出所有浏览器 -> 删除 PlatformExperienceHelper 目录 -> 删除对应 CLSID')
    [void]$report.Add('    注册表项 -> 清理 %TEMP% 下 k2f-* / insight.bundle 残留 -> 全盘查杀')
    [void]$report.Add(' 6. 顺带核查 Launcher 目录是否出现 service_util.dll / old-clsid.txt（变种迹象）')
    [void]$report.Add(' 7. 样本上报：把上面命中项里的 SHA256 提交 VirusTotal，并向厂商/韩国官方反馈')
    [void]$report.Add(' 注意：只删 DLL 不等于安全。DLL 一旦被 chrome.exe 加载过，本机所有明文凭据都应按已泄露处理。')
} else {
    [void]$report.Add(' 结论说明')
    [void]$report.Add('================================================================')
    if ($warns.Count -gt 0) {
        [void]$report.Add(' 存在 ' + $warns.Count + ' 项待确认的弱证据 / 可疑痕迹，但均不能单独证明感染：')
        [void]$report.Add('   - hosts/DNS/浏览器历史中出现 insightjs：可能只是你查询过该域名、或访问过分析文章')
        [void]$report.Add('   - Launcher 在 2026-09-27 之后启动过：只是时间线索，不等于中招')
        [void]$report.Add('   - 临时目录有 k2f-* 文件：需确认是否为 k2f-bcreds-*（强证据）')
        [void]$report.Add(' 请逐条核对上面带 [WARN] 的条目；只有出现强证据（已知哈希 / 恶意 CLSID /')
        [void]$report.Add(' 日志确认 k2f_chan 执行 / 已知恶意 IP 活动连接）才需要按失陷处置。')
        [void]$report.Add('')
    }
    [void]$report.Add(' 未发现本次 IOC 的直接痕迹。需要说明的两点：')
    [void]$report.Add(' 1. Chrome 自带的 platform_experience_helper.exe（位于 Program Files，Google 签名，')
    [void]$report.Add('    计划任务路径 \GoogleUserPEH\）是官方组件，与恶意样本同名但不是同一个东西，')
    [void]$report.Add('    恶意样本是放在 User Data 下的同名 DLL，靠路径 + 签名区分。')
    [void]$report.Add(' 2. 若 Launcher 日志最后写入时间早于 2026-09-27（域名抢注日），说明本次恶意活动')
    [void]$report.Add('    窗口内未启动过 Launcher，通过该链路失陷的可能性很低。')
    [void]$report.Add('')
    [void]$report.Add(' 仍建议：保持浏览器与杀软更新；不要安装来源不明的 Launcher/补丁/汉化。')
}
[void]$report.Add('')

$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
if (-not (Test-Path -LiteralPath $OutDir)) { try { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null } catch { $OutDir = $env:TEMP } }
$reportPath = Join-Path $OutDir ('nikke_ioc_scan_' + $stamp + '.txt')
try {
    ($report -join "`r`n") | Out-File -FilePath $reportPath -Encoding UTF8
} catch {
    $reportPath = Join-Path $env:TEMP ('nikke_ioc_scan_' + $stamp + '.txt')
    ($report -join "`r`n") | Out-File -FilePath $reportPath -Encoding UTF8
}

Write-Host ''
Write-Host ('=' * 70) -ForegroundColor DarkCyan
$vColor = 'Green'; if ($hits.Count -gt 0) { $vColor = 'Red' } elseif ($warns.Count -gt 0) { $vColor = 'Yellow' }
Write-Host ('  结论：' + $verdict) -ForegroundColor $vColor
Write-Host ('  命中 ' + $hits.Count + '  |  待确认 ' + $warns.Count + '  |  正常 ' + $cleans.Count + '  |  用时 ' + [math]::Round(((Get-Date) - $script:StartTime).TotalSeconds, 1) + ' 秒') -ForegroundColor Gray
Write-Host ('  报告：' + $reportPath) -ForegroundColor Cyan
if (-not $script:IsAdmin) {
    Write-Host '  提示：当前非管理员，注册表 HKLM / 部分进程模块检查可能不完整' -ForegroundColor Yellow
}
Write-Host ('=' * 70) -ForegroundColor DarkCyan

$important = @($hits + $warns)
if ($important.Count -gt 0) {
    Write-Host '  需处理/核对内容（HIT / WARN）' -ForegroundColor Cyan
    Write-Host ('-' * 70) -ForegroundColor DarkCyan
    foreach ($f in $important) {
        $color = 'Yellow'; if ($f.Level -eq 'HIT') { $color = 'Red' }
        Write-Host ('[' + $f.Level + '] [' + $f.Category + '] ' + $f.Title) -ForegroundColor $color
        if ($f.Evidence) {
            foreach ($line in ($f.Evidence -split "`r?`n")) {
                if ($line.Trim().Length -gt 0) { Write-Host ('    ' + $line) -ForegroundColor DarkGray }
            }
        }
        Write-Host ''
    }
} else {
    Write-Host '  无需处理或人工核对：未发现 HIT / WARN 项。' -ForegroundColor Green
}
Write-Host ('=' * 70) -ForegroundColor DarkCyan
Write-Host ''

}

function Invoke-NikkeRemediation {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
    param([switch]$Execute, [switch]$BlockDomains, [string]$QuarantineRoot, [string]$TargetPath)

$ErrorActionPreference = 'Stop'
$knownHashes = @(
    '3507AFE81982D19795CAD7280072A42A5A8F5AD5ECE22C48DC5627B7E9CCBF4B',
    '931747D56B9639829AECD5540991D53DF336FF4E93B30C7DB0C8DF7A6038942E',
    '4BFAF98A6B863FA79554D9EEEB18870C3F92D552D62F4BD35A72D19EA68526AF'
)
if (-not $TargetPath) {
    $browserRoots = @(
        "$env:LOCALAPPDATA\Google\Chrome\User Data\PlatformExperienceHelper",
        "$env:LOCALAPPDATA\Google\Chrome Beta\User Data\PlatformExperienceHelper",
        "$env:LOCALAPPDATA\Google\Chrome Dev\User Data\PlatformExperienceHelper",
        "$env:LOCALAPPDATA\Microsoft\Edge\User Data\PlatformExperienceHelper",
        "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data\PlatformExperienceHelper",
        "$env:LOCALAPPDATA\Vivaldi\User Data\PlatformExperienceHelper"
    )
    $confirmed = @()
    foreach ($root in ($browserRoots | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $candidates = @(Get-ChildItem -LiteralPath $root -Recurse -File -Filter 'platform_experience_helper.dll' -ErrorAction SilentlyContinue)
        foreach ($candidate in $candidates) {
            $hash = (Get-FileHash -LiteralPath $candidate.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
            if ($knownHashes -contains $hash) { $confirmed += $candidate.FullName }
        }
    }
    $confirmed = @($confirmed | Sort-Object -Unique)
    if ($confirmed.Count -gt 1) {
        throw "发现多个已确认目标，拒绝任意选择。请使用 -TargetPath 指定：$($confirmed -join ', ')"
    }
    if ($confirmed.Count -eq 1) { $TargetPath = $confirmed[0] }
}
$clsidPath = 'HKCU:\Software\Classes\CLSID\{E77CC89B-7401-4C04-8CED-149DB35ADD04}'
$domains = @('analytics.insightjs.com', 'insightjs.com', 'insightjs.org')
$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
$failures = New-Object System.Collections.Generic.List[string]
$evidence = New-Object System.Collections.Generic.List[object]

function Test-TargetIdentity {
    param([string]$Path)
    $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($knownHashes -contains $hash) { return $hash }
    throw "目标哈希不在已确认列表中，拒绝处理：$hash"
}

function Get-LoadedProcessIds {
    if (-not $targetPath) { return @() }
    $ids = @()
    foreach ($process in (Get-Process -ErrorAction SilentlyContinue)) {
        try {
            if ($process.Modules.FileName -contains $targetPath) { $ids += $process.Id }
        }
        catch {}
    }
    return @($ids | Sort-Object -Unique)
}

function Get-NikkeProcesses {
    $processes = @()
    foreach ($name in @('nikke_launcher', 'nikke_launcher_hmt')) {
        $processes += @(Get-Process -Name $name -ErrorAction SilentlyContinue)
    }
    foreach ($process in (Get-Process -Name 'launcher-x64' -ErrorAction SilentlyContinue)) {
        try {
            if ($process.Path -match '(?i)^\w:\\NIKKE(?:_HMT)?\\') { $processes += $process }
        }
        catch {}
    }
    return @($processes | Sort-Object Id -Unique)
}

$targetExists = [bool]($targetPath -and (Test-Path -LiteralPath $targetPath -PathType Leaf))
$targetHash = $null
if ($targetExists) {
    $targetHash = Test-TargetIdentity -Path $targetPath
}

$comExists = Test-Path -LiteralPath $clsidPath
$comEvidence = ''
if ($comExists) {
    foreach ($server in @('InprocServer32', 'LocalServer32')) {
        $serverPath = Join-Path $clsidPath $server
        if (Test-Path -LiteralPath $serverPath) {
            $value = [string](Get-ItemProperty -LiteralPath $serverPath).'(default)'
            if ($value) { $comEvidence += "$server = $value`r`n" }
        }
    }
    if ($comEvidence -notmatch '(?i)platform_experience_helper\.dll') {
        throw 'CLSID 存在，但未指向目标 DLL，拒绝自动删除。'
    }
}

$loadedProcessIds = @(Get-LoadedProcessIds)
$nikkeProcesses = @(Get-NikkeProcesses)
$missingDomains = @()
if ($BlockDomains) {
    $hostsText = [System.IO.File]::ReadAllText($hostsPath)
    $missingDomains = @($domains | Where-Object {
        $hostsText -notmatch "(?im)^\s*(?:0\.0\.0\.0|127\.0\.0\.1|::)\s+$([regex]::Escape($_))(?:\s|$)"
    })
}

Write-Host '=== 处理计划 ===' -ForegroundColor Cyan
Write-Host "恶意 DLL：$(if ($targetExists) { $targetPath } else { '不存在，无需删除' })"
if ($targetHash) { Write-Host "SHA256：$targetHash`n处理方式：直接删除，不保留 DLL 副本" }
Write-Host "COM 注册项：$(if ($comExists) { $clsidPath } else { '不存在，无需删除' })"
if ($comEvidence) { Write-Host $comEvidence.TrimEnd() }
Write-Host "占用 DLL 的进程：$(if ($loadedProcessIds.Count) { '将结束 PID ' + ($loadedProcessIds -join ', ') } else { '未发现已加载目标的进程' })"
foreach ($process in $nikkeProcesses) {
    Write-Host "NIKKE 进程：将结束 $($process.ProcessName) PID=$($process.Id)"
}
if ($BlockDomains) {
    Write-Host "hosts 封锁：$($missingDomains -join ', ')"
    if ($missingDomains.Count -eq 0) { Write-Host '三个域名均已有封锁记录，无需修改 hosts。' }
}

if (-not $Execute) {
    Write-Host "`n当前仅为预览，未修改系统。确认计划后使用 -Execute；需要域名封锁时再加 -BlockDomains。" -ForegroundColor Green
    return
}

if (-not $targetExists -and -not $comExists -and $missingDomains.Count -eq 0) {
    Write-Host '没有需要处理的项目。'
    return
}

if ($missingDomains.Count -gt 0) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) { throw 'hosts 封锁需要管理员权限。也可省略 -BlockDomains，仅清理 DLL 和 CLSID。' }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $QuarantineRoot) {
    $base = $(if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path })
    $QuarantineRoot = Join-Path $base "quarantine-$stamp"
}
if (-not $PSCmdlet.ShouldProcess($QuarantineRoot, '创建备份与隔离目录')) { throw '用户取消操作。' }
New-Item -ItemType Directory -Path $QuarantineRoot -Force | Out-Null

if ($targetExists) {
    $evidence.Add([pscustomobject]@{ Type = 'DLL'; Original = $targetPath; Backup = $null; SHA256 = $targetHash })
}

$registryBackedUp = $false
if ($comExists) {
    $registryBackup = Join-Path $QuarantineRoot 'malicious-clsid.reg'
    $nativeClsidPath = $clsidPath -replace '^HKCU:\\', 'HKCU\'
    if ($PSCmdlet.ShouldProcess($clsidPath, "备份注册表到 $registryBackup")) {
        & reg.exe export $nativeClsidPath $registryBackup /y | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'CLSID 备份失败，停止删除注册表项。' }
        $registryBackedUp = $true
        $evidence.Add([pscustomobject]@{ Type = 'Registry'; Original = $clsidPath; Backup = $registryBackup })
    }
}

$hostsBackedUp = $false
if ($missingDomains.Count -gt 0) {
    $hostsBackup = Join-Path $QuarantineRoot 'hosts.backup'
    if ($PSCmdlet.ShouldProcess($hostsPath, "备份 hosts 到 $hostsBackup")) {
        Copy-Item -LiteralPath $hostsPath -Destination $hostsBackup
        $hostsBackedUp = $true
        $evidence.Add([pscustomobject]@{ Type = 'Hosts'; Original = $hostsPath; Backup = $hostsBackup })
    }
}

$loadedProcessIds = @($loadedProcessIds + (Get-LoadedProcessIds) | Sort-Object -Unique)
foreach ($id in $loadedProcessIds) {
    $processName = (Get-Process -Id $id -ErrorAction SilentlyContinue).ProcessName
    if ($PSCmdlet.ShouldProcess("$processName PID=$id", '结束所有占用该 DLL 的进程')) {
        try { Stop-Process -Id $id -Force -ErrorAction Stop }
        catch { $failures.Add("结束进程 PID=$id 失败：$($_.Exception.Message)") }
    }
}
foreach ($process in $nikkeProcesses) {
    if ($PSCmdlet.ShouldProcess("$($process.ProcessName) PID=$($process.Id)", '结束 NIKKE 启动器以防再次投递')) {
        try { Stop-Process -Id $process.Id -Force -ErrorAction Stop }
        catch { $failures.Add("结束 NIKKE PID=$($process.Id) 失败：$($_.Exception.Message)") }
    }
}
Start-Sleep -Milliseconds 1000

if ($targetExists) {
    if ($PSCmdlet.ShouldProcess($targetPath, '直接删除已确认哈希的恶意 DLL，不保留文件副本')) {
        try {
            Remove-Item -LiteralPath $targetPath -Force -ErrorAction Stop
            $parent = Split-Path -Parent $targetPath
            if (-not (Get-ChildItem -LiteralPath $parent -Force -ErrorAction SilentlyContinue)) {
                Remove-Item -LiteralPath $parent -Force -ErrorAction SilentlyContinue
            }
        }
        catch { $failures.Add("删除 DLL 失败：$($_.Exception.Message)") }
    }
}

if ($comExists) {
    if (-not $registryBackedUp) {
        $failures.Add('CLSID 未删除：未成功备份。')
    }
    elseif ($PSCmdlet.ShouldProcess($clsidPath, '删除已备份的恶意 CLSID')) {
        try { Remove-Item -LiteralPath $clsidPath -Recurse -Force -ErrorAction Stop }
        catch { $failures.Add("删除 CLSID 失败：$($_.Exception.Message)") }
    }
}

if ($missingDomains.Count -gt 0) {
    if (-not $hostsBackedUp) {
        $failures.Add('hosts 未修改：未成功备份。')
    }
    elseif ($PSCmdlet.ShouldProcess($hostsPath, "封锁 $($missingDomains -join ', ')")) {
        try {
            $block = "`r`n# NIKKE insightjs remediation $stamp`r`n" + (($missingDomains | ForEach-Object { "0.0.0.0 $_" }) -join "`r`n") + "`r`n"
            Add-Content -LiteralPath $hostsPath -Value $block -Encoding ASCII -ErrorAction Stop
        }
        catch { $failures.Add("hosts 封锁失败：$($_.Exception.Message)") }
    }
}

$manifest = Join-Path $QuarantineRoot 'manifest.json'
[pscustomobject]@{
    Time = (Get-Date).ToString('o')
    Evidence = $evidence
    Failures = $failures
} | ConvertTo-Json -Depth 5 | Out-File -LiteralPath $manifest -Encoding UTF8

if ($targetExists -and (Test-Path -LiteralPath $targetPath)) { $failures.Add("原 DLL 仍存在：$targetPath") }
if ($comExists -and (Test-Path -LiteralPath $clsidPath)) { $failures.Add("CLSID 仍存在：$clsidPath") }

Write-Host "`n=== 处理结果 ===" -ForegroundColor Cyan
if ($failures.Count -eq 0) {
    Write-Host "处理完成。处理记录与备份：$QuarantineRoot" -ForegroundColor Green
}
else {
    $failures | ForEach-Object { Write-Host "[未完成] $_" -ForegroundColor Red }
    Write-Host "处理记录与备份：$QuarantineRoot"
}
Write-Host '清理不能撤销已经发生的数据泄露。请在可信设备上修改密码、注销会话、轮换开发令牌；钱包需新建助记词并转移资产。' -ForegroundColor Yellow

}


function Test-IsAdministrator {
    try {
        return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    catch {
        return $false
    }
}

if (-not (Test-IsAdministrator)) {
    Write-Host '需要管理员权限，即将打开 UAC 提权窗口。' -ForegroundColor Yellow
    try {
        $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -WorkingDirectory $PSScriptRoot
    }
    catch {
        Write-Host "未取得管理员权限，已退出：$($_.Exception.Message)" -ForegroundColor Red
        [void](Read-Host '按 Enter 关闭')
    }
    return
}

function Wait-MenuKey {
    [void](Read-Host "`n按 Enter 返回菜单")
}

function Invoke-ScanMenu {
    param([switch]$Full)
    Invoke-NikkeScan -OutDir $reportRoot -Full:$Full
}

function Invoke-CleanupMenu {
    param([switch]$BlockDomains)
    if ($BlockDomains -and -not (Test-IsAdministrator)) {
        Write-Host '域名封锁需要管理员权限。请关闭本窗口，以管理员身份重新运行。' -ForegroundColor Red
        return
    }

    Invoke-NikkeRemediation
    if (-not $BlockDomains) {
        Write-Host "`n仅处理已确认 DLL 和 CLSID。输入 CLEAN 确认执行：" -ForegroundColor Yellow
        $confirmation = Read-Host
        if ($confirmation -ceq 'CLEAN') {
            Invoke-NikkeRemediation -Execute -Confirm:$false
        }
        else {
            Write-Host '已取消，未执行处理。' -ForegroundColor Green
        }
        return
    }

    Write-Host "`n将处理 DLL、CLSID，并在备份 hosts 后封锁三个域名。输入 CLEAN+BLOCK 确认执行：" -ForegroundColor Yellow
    $confirmation = Read-Host
    if ($confirmation -ceq 'CLEAN+BLOCK') {
        Invoke-NikkeRemediation -Execute -BlockDomains -Confirm:$false
    }
    else {
        Write-Host '已取消，未执行处理。' -ForegroundColor Green
    }
}

while ($true) {
    Write-Host "`n============================================================" -ForegroundColor DarkCyan
    Write-Host '  NIKKE / insightjs 扫描与处理' -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor DarkCyan
    Write-Host "  管理员权限：$(if (Test-IsAdministrator) { '是' } else { '否' })"
    Write-Host '  1. 快速 IOC 扫描'
    Write-Host '  2. 全盘 IOC 扫描（较慢）'
    Write-Host '  3. 查看处理计划（只读）'
    Write-Host '  4. 处理恶意 DLL 和 CLSID'
    Write-Host '  5. 处理 DLL、CLSID 并封锁恶意域名'
    Write-Host '  0. 退出'
    Write-Host '------------------------------------------------------------'
    $choice = Read-Host '请选择'

    try {
        switch ($choice.Trim()) {
            '1' {
                Invoke-ScanMenu
                Wait-MenuKey
            }
            '2' {
                Write-Host '全盘扫描可能持续数分钟。输入 FULL 确认：' -ForegroundColor Yellow
                if ((Read-Host) -ceq 'FULL') {
                    Invoke-ScanMenu -Full
                }
                else {
                    Write-Host '已取消全盘扫描。'
                }
                Wait-MenuKey
            }
            '3' {
                Invoke-NikkeRemediation
                Wait-MenuKey
            }
            '4' {
                Invoke-CleanupMenu
                Wait-MenuKey
            }
            '5' {
                Invoke-CleanupMenu -BlockDomains
                Wait-MenuKey
            }
            '0' { return }
            default {
                Write-Host '无效选项，请重新输入。' -ForegroundColor Yellow
            }
        }
    }
    catch {
        Write-Host "操作失败：$($_.Exception.Message)" -ForegroundColor Red
        Wait-MenuKey
    }
}
