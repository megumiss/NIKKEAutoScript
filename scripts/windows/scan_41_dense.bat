@echo off
rem 41 章密集扫描（提权）。游戏以管理员运行时，扫描器必须同级权限才能调整窗口与输入。
rem 双击后会弹一次 UAC，点是即可；扫描期间不要动游戏窗口和鼠标，完成后窗口会停留在结果页。
powershell -NoProfile -Command "Start-Process -Verb RunAs -Wait -WorkingDirectory 'D:\PCR\NIKKEAutoScript' -FilePath 'cmd.exe' -ArgumentList '/c','.venv\Scripts\python.exe -X utf8 -m dev_tools.minimap_chapters --start 41 --end 41 --keyframe-px 25 --stroke-px 120 --retries 0 --output data/chapter_maps/local/dense > log\dense_41_scan.log 2>&1 & echo. & echo === scan finished, see log\dense_41_scan.log === & pause'"
