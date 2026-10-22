@echo off
rem 48-1 章全量密集扫描：只采集，不重建（提权）。重建由 tmp\scan48\rebuild_dense01_48.py 离线补齐。
rem 游戏以管理员运行时，调整窗口尺寸需要同级权限；客户区已是 1776×999 且无需移动时，
rem 普通权限也能跑（本次会话即以普通权限直接运行）。
rem keyframe-px 25 把相邻帧位移压到约 0.42 个网格周期（48px），低于半格消歧上限；与 dense30 同参数。
rem 批次倒序采集：开始前游戏必须停在 --start 指定章的田野页面，每章完成后自动点"上一章"切到下一章。
rem 中断后把 --start 改成游戏当前所停章节重跑即可续采（扫描已完整的章节自动跳过）；
rem 要中途停止则在输出目录建 STOP 文件，批次会在章节边界退出。
rem 双击后会弹一次 UAC，点是即可；扫描期间不要动游戏窗口和鼠标，完成后窗口会停留在结果页。
powershell -NoProfile -Command "Start-Process -Verb RunAs -Wait -FilePath 'cmd.exe' -ArgumentList '/c','cd /d D:\PCR\NIKKEAutoScript && .venv\Scripts\python.exe -X utf8 -m dev_tools.campaign.capture.chapters --start 48 --end 1 --keyframe-px 25 --stroke-px 120 --retries 0 --capture-only --output data\chapter_maps\local\dense01_48 > log\dense_01_48_capture.log 2>&1 & echo. & echo === capture finished, see log\dense_01_48_capture.log === & pause'"
