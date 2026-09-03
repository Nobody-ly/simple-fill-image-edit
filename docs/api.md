# HTTP API

API 默认位于 `http://127.0.0.1:7862`。FastAPI 交互文档位于 `/docs`。

## 健康检查

```http
GET /api/health
```

## 项目

创建项目：

```http
POST /api/projects
Content-Type: multipart/form-data

name=<项目名>
image=<PNG/JPEG/WebP>
```

列出与读取：

```http
GET /api/projects
GET /api/projects/{project_id}
```

## 语义分割

```http
POST /api/projects/{project_id}/segment
Content-Type: application/json
```

```json
{
  "prompt": "人物手中的书",
  "points": [],
  "boxes": [],
  "source_ref": "source"
}
```

成功返回 `mask_id`、覆盖率和蒙版预览地址。文字与空间提示不应混合：界面有框时发送 box，没有框时发送文字。

## 直接框选蒙版

不调用 SAM3，把用户在原图坐标系中确认的矩形直接保存为可编辑蒙版：

```http
POST /api/projects/{project_id}/regions
Content-Type: application/json
```

```json
{
  "box": {"x_min": 390, "y_min": 530, "x_max": 650, "y_max": 820},
  "label": "人物手中的书",
  "source_ref": "source"
}
```

返回记录的 `provider.provider` 为 `manual-region-mask`，`sam3_used` 为 `false`。坐标会裁切到图片边界，宽或高小于 4 px 的区域会被拒绝。

## 生成

```http
POST /api/projects/{project_id}/generate
Content-Type: application/json
```

```json
{
  "operation": "fill",
  "mask_id": "mask_xxx",
  "prompt": "把书替换成一只被双手抱住的白色长毛猫",
  "dilation": 6,
  "growth_ratio": 0.15,
  "feather": 3,
  "pipeline_mode": "simple_fill"
}
```

使用用户框选蒙版时推荐 `growth_ratio: 0.15`，避免比旧对象更大的耳朵、镜框或装饰边缘在回填时被裁断；文字替换等严格区域可使用 `0`。`dilation: 6` 仍会额外清理紧贴边界的旧轮廓。使用 SAM3 对象蒙版时可保留 `growth_ratio: 0.35`。

提交立即返回任务，后台线程继续执行。轮询：

```http
GET /api/projects/{project_id}/tasks/{task_id}
```

状态为 `created`、`generating`、`completed` 或 `failed`。

## 重试与下载

```http
POST /api/projects/{project_id}/tasks/{task_id}/retry
GET  /api/projects/{project_id}/versions/{version_id}/download
```

`retry` 会创建一个带 `retry_of` 的新任务，并再次调用付费图像服务。当前通用适配器没有跨进程供应商 job id，因此不会把不确定的超时伪装成安全恢复。
