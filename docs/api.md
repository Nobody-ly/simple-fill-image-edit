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

成功返回 `mask_id`、覆盖率和蒙版预览地址。文字与空间提示不应混合；界面默认使用文字。

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
  "growth_ratio": 0.35,
  "feather": 3,
  "pipeline_mode": "simple_fill"
}
```

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
