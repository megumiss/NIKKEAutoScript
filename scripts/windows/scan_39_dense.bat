@echo off
rem 39 章密集扫描（提权）。游戏以管理员运行时，扫描器必须同级权限才能调整窗口与输入。
rem keyframe-px 25 把相邻帧位移压到约 0.42 个网格周期（48px），低于半格消歧上限；
rem 原有 39 章包是稀疏采集，平面重建运动标定样本不足（3/12），加密帧数增加合格帧对。
rem 双击后会弹一次 UAC，点是即可；扫描期间不要动游戏窗口和鼠标，完成后窗口会停留在结果页。
powershell -NoProfile -Command "Start-Process -Verb RunAs -Wait -WorkingDirectory 'D:\PCR\NIKKEAutoScript' -FilePath 'cmd.exe' -ArgumentList '/c','.venv\Scripts\python.exe -X utf8 -m dev_tools.minimap_chapters --start 39 --end 39 --keyframe-px 25 --stroke-px 120 --retries 0 --output data/chapter_maps/local/dense39 > log\dense_39_scan.log 2>&1 & echo. & echo === scan finished, see log\dense_39_scan.log === & pause'"
