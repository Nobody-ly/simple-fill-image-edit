# 封装前版本使用交接

## 路线不要混用

### Simple Fill

用于普通对象替换、颜色修改和整块文字重绘。最佳白猫历史基线使用 SAM3 `book`、dilation 6、growth 0.35、feather 3；没有先用 LaMa 删除书，也没有生成后再次切猫。

### Fill Anything / Legacy

用于候选窗口可能有无关变化、且新对象能被 SAM3 稳定识别的任务。历史星星眼镜在生成后再次以 `glasses` 分割候选，再构造 commit mask。

### Object V2

包含 Big-LaMa clean plate、protected mask、结果对象分割和 trimap/alpha。仅在旧对象残影或复杂遮挡确有需要时使用；更复杂不代表必然更自然。

该分支依赖目标机器匹配 CUDA 的 PyTorch 和外部 Inpaint Anything/Big-LaMa 目录。仓库 `requirements.txt` 不负责选择 CUDA 版 `torch`；缺少它时健康检查会失败，这是环境未完成，不应静默禁用 Object V2。

## 共同处理链

```text
source + SAM3/人工 mask → generation envelope
→ Inpaint Anything 512×512 crop → gpt-image-2 high 原生 alpha mask
→ 恢复原图坐标 → 当前分支 commit mask → 软边融合
→ 保存 task、provider 原图、中间蒙版和 version
```

透明 alpha 表示可编辑，不透明表示保护。小于服务最低画布时，局部窗口和蒙版一起放大到 1024×1024。

## 固定 Image2 保护前缀

```text
只修改透明蒙版指定的局部区域，保留其余构图、人物、姿态、透视、光照和视觉关系。
让修改内容在边界处与原图自然衔接。目标内容：
```

## SAM3

- 中文优先转换为短英文：书 `book`、眼镜 `glasses`、花瓶 `vase`、手 `hand`。
- 有点/框时不再发送文本，避免混用返回空蒙版。
- 必须检查 coverage、bbox 和预览。

## 遮挡和边界

- 新对象更大时扩大 generation envelope，而不是只增加 feather。
- provider 候选已截断：generation mask 问题。
- provider 完整但最终截断：commit mask 问题。
- 旧轮廓残留：检查 target mask/dilation；过大 feather 会混回旧边缘。
- 复杂遮挡优先让 Image2 在完整窗口一次生成空间关系；硬保护手部可能截断新对象。

## 远端恢复

`/srv/catsco-agent/work/inpaint-anything-runs/<task_id>-native-mask`

SSH 超时后先检查结果 PNG 和响应 JSON；已存在时恢复同一任务，不重新提交。

## 记录要求

保存 prompt、source/mask、dilation、growth、feather、pipeline、lane、request ID、远端目录、fallback、provider 原图、commit mask 和最终 version。

人工扩大范围或复用付费结果时记录 `reprocess_of_task`、`reused_paid_generation`、`composition_policy`，不能描述为全自动成功。
