# 配置参考

所有配置从环境变量或仓库根目录 `.env` 读取。

## 图像编辑服务

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IMAGE_API_BASE_URL` | `https://api.openai.com/v1` | 基础地址；也可直接填写以 `/images/edits` 结尾的地址 |
| `IMAGE_API_KEY` | 空 | Bearer Token，必填 |
| `IMAGE_MODEL` | `gpt-image-2` | 服务端模型名 |
| `IMAGE_FIELD` | `image[]` | multipart 中原图字段名；部分兼容层使用 `image` |
| `IMAGE_API_TRANSPORT` | `multipart` | `multipart` 或 `json-data-url` |
| `IMAGE_API_AUTH_SCHEME` | `Bearer` | `Bearer` 或 `ApiKey` |
| `IMAGE_ROUTE_HEADER_NAME` | 空 | 可选的网关路由请求头名 |
| `IMAGE_ROUTE_HEADER_VALUE` | 空 | 可选的网关路由请求头值；必须和请求头名同时设置 |
| `IMAGE_TIMEOUT_SECONDS` | `600` | 请求总超时 |

接口请求包含：

```text
model, prompt, size, quality=high, output_format=png
image/image[], mask
```

返回格式支持：

```json
{"data":[{"b64_json":"..."}]}
```

或带 HTTPS 临时地址：

```json
{"data":[{"url":"https://..."}]}
```

若服务字段或响应不同，只需实现/修改 `app/image_provider.py`，不要把凭据写入代码。

## SAM3

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WAVESPEED_BASE_URL` | `https://api.wavespeed.ai/api/v3` | WaveSpeed API 地址 |
| `WAVESPEED_API_KEY` | 空 | WaveSpeed Access Key，必填 |

适配器位于 `app/sam3_wavespeed.py`，使用上传票据和异步 prediction 轮询，不会把 Access Key 返回给浏览器。

## 应用与存储

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SIMPLE_FILL_HOST` | `127.0.0.1` | 监听地址；容器部署常用 `0.0.0.0` |
| `SIMPLE_FILL_PORT` | `7862` | HTTP 端口 |
| `SIMPLE_FILL_WORKERS` | `3` | 同进程后台任务线程数 |
| `SIMPLE_FILL_DATA` | `./data` | 项目持久化目录 |
| `MAX_UPLOAD_MIB` | `200` | 单图文件大小上限 |
| `MAX_IMAGE_PIXELS` | `120000000` | 解码后像素总数上限，防止图像解压炸弹 |

## 生产部署建议

- 把 API Key 放入平台 Secret Manager，而不是 `.env` 镜像层。
- 使用反向代理提供 HTTPS、身份认证、请求体限制和速率限制。
- 把 `SIMPLE_FILL_DATA` 挂载到持久卷；多副本部署时改成数据库 + 对象存储。
- 任务执行器当前是进程内线程池；生产环境可替换成 Celery、RQ 或云队列。
- 如果供应商支持异步 job id，可扩展真正的“恢复同一任务”，避免网络超时后重复计费。
