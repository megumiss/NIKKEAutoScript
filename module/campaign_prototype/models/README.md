# 战役 YOLO 第四版

`campaign.onnx` 与 `campaign.json` 成对分发。运行时验证 SHA-256、类别顺序、输入尺寸及输出格式；模型缺失或不匹配时明确失败。

- 范围：中文 Windows `1776×999`，场景白／橙箭头、收集提示、展开／紧凑小地图圆环和普通关卡；EX 分类作为辅助。
- 模型：YOLO11n，第四版训练第 20 轮最佳权重；FP32 ONNX opset 17，输入 `1×3×640×640`，各类别阈值为 `0.5`。
- 普通关卡验证：15 张未用于训练、采集目录隔离的截图中，37 个完整普通目标全部检出，检测误报 0；动作层输出 36 个有效中心、误选 0。另 1 个完整目标的预测框距上边缘约 2.95 px，被 3 px 边界保护拒绝。
- 样本限制：37 个普通目标来自 3 张相关正样本，另 12 张用于干扰验证；同尺寸训练图的感知哈希距离至少为 10。该结果不等于任意章节、语言或分辨率下的准确率保证。
- EX 排除：敌人移动模式保留普通战斗准备弹窗；确认 `EX STAGE` 后关闭一次、确认关闭并返回 `ex_stage_skipped`。关闭失败终止，本次移动不进入战斗。

模型验证摘要与哈希见 [validation_v4.json](../../../dev_tools/campaign_yolo/validation_v4.json)。训练环境与截图不随模型分发。

从仓库根目录安装运行依赖：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-yolo.txt
```

模型由运行会话首次检测时加载并复用，无需安装 PyTorch 或 Ultralytics。

## 九类离线模型

2026-10-08 已单独导出九类v6，加入机关off/on和电梯起点／终点。模型及测试说明见 [训练项目](E:/CodexData/NIKKEAutoScript/yolo/README.md)。本目录仍分发五类v4；九类输出未接入当前运行加载器，不可直接覆盖此处模型。
