# 可直接复制的完整配置

这份文档从一台没有安装过项目的机器开始。命令、目录和环境变量可以直接复制；只有两个密钥和图像接口地址需要替换成你自己的值。

## 一、Windows 本地运行

要求：Windows 10/11、Python 3.11 或 3.12、Git。

```powershell
git clone https://github.com/Nobody-ly/simple-fill-image-edit.git
Set-Location simple-fill-image-edit
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

用文本编辑器打开 `.env`，粘贴下面内容：

```dotenv
# 1. 支持原生 mask 的 OpenAI-compatible 图像编辑接口
IMAGE_API_BASE_URL=https://your-image-gateway.example.com/v1
IMAGE_API_KEY=替换成你的图像服务密钥
IMAGE_MODEL=gpt-image-2
IMAGE_FIELD=image[]
IMAGE_TIMEOUT_SECONDS=600

# 2. WaveSpeed SAM3
WAVESPEED_BASE_URL=https://api.wavespeed.ai/api/v3
WAVESPEED_API_KEY=替换成你的WaveSpeed密钥

# 3. 本地工作台
SIMPLE_FILL_HOST=127.0.0.1
SIMPLE_FILL_PORT=7862
SIMPLE_FILL_WORKERS=3
SIMPLE_FILL_DATA=./data
MAX_UPLOAD_MIB=200
MAX_IMAGE_PIXELS=120000000
```

然后启动：

```powershell
python run.py
```

浏览器打开：<http://127.0.0.1:7862>

停止服务：在运行窗口按 `Ctrl+C`。

## 二、Linux 服务器运行

```bash
git clone https://github.com/Nobody-ly/simple-fill-image-edit.git
cd simple-fill-image-edit
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

`.env` 使用下面配置；服务器监听地址改成 `0.0.0.0`：

```dotenv
IMAGE_API_BASE_URL=https://your-image-gateway.example.com/v1
IMAGE_API_KEY=替换成你的图像服务密钥
IMAGE_MODEL=gpt-image-2
IMAGE_FIELD=image[]
IMAGE_TIMEOUT_SECONDS=600

WAVESPEED_BASE_URL=https://api.wavespeed.ai/api/v3
WAVESPEED_API_KEY=替换成你的WaveSpeed密钥

SIMPLE_FILL_HOST=0.0.0.0
SIMPLE_FILL_PORT=7862
SIMPLE_FILL_WORKERS=3
SIMPLE_FILL_DATA=/opt/simple-fill/data
MAX_UPLOAD_MIB=200
MAX_IMAGE_PIXELS=120000000
```

启动：

```bash
python run.py
```

`/opt/simple-fill/data` 必须给运行用户写权限。正式公网部署应在前面增加 HTTPS 反向代理和身份认证，不要直接裸露 7862 端口。

## 三、Docker Compose

在仓库目录创建 `.env`，内容与上面相同，但推荐把数据目录保持为容器默认值：

```dotenv
IMAGE_API_BASE_URL=https://your-image-gateway.example.com/v1
IMAGE_API_KEY=替换成你的图像服务密钥
IMAGE_MODEL=gpt-image-2
IMAGE_FIELD=image[]
IMAGE_TIMEOUT_SECONDS=600

WAVESPEED_BASE_URL=https://api.wavespeed.ai/api/v3
WAVESPEED_API_KEY=替换成你的WaveSpeed密钥

SIMPLE_FILL_HOST=0.0.0.0
SIMPLE_FILL_PORT=7862
SIMPLE_FILL_WORKERS=3
SIMPLE_FILL_DATA=/app/data
MAX_UPLOAD_MIB=200
MAX_IMAGE_PIXELS=120000000
```

运行：

```bash
docker compose up -d --build
docker compose logs -f simple-fill
```

项目、原图、蒙版、结果和任务历史会保存在宿主机仓库的 `data/` 目录。

## 四、图像接口必须满足的协议

`IMAGE_API_BASE_URL` 必须指向 HTTPS 的 OpenAI-compatible 服务。项目实际调用：

```http
POST {IMAGE_API_BASE_URL}/images/edits
Authorization: Bearer {IMAGE_API_KEY}
Content-Type: multipart/form-data
```

multipart 字段：

| 字段 | 内容 |
|---|---|
| `model` | `IMAGE_MODEL` |
| `prompt` | 范围保护指令 + 用户生成要求 |
| `size` | 裁剪图的 `宽x高` |
| `quality` | `high` |
| `output_format` | `png` |
| `image[]` | PNG 原图；字段名由 `IMAGE_FIELD` 控制 |
| `mask` | RGBA PNG，透明像素为允许编辑区域 |

响应必须是以下任一种：

```json
{"data":[{"b64_json":"PNG_BASE64"}]}
```

```json
{"data":[{"url":"https://temporary-result.example/result.png"}]}
```

如果你的接口把图片字段命名为 `image`，只需修改：

```dotenv
IMAGE_FIELD=image
```

如果服务只接受固定的 `1024x1024`，当前链路可以正常工作，因为默认 Inpaint Anything 裁剪会被送成方形；供应商返回后会归一化并回填到原图坐标。

## 五、启动后先做配置检查

PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:7862/api/health | ConvertTo-Json
```

Linux / macOS：

```bash
curl -s http://127.0.0.1:7862/api/health
```

正常配置应包含：

```json
{
  "ok": true,
  "simple_semantic_fill": true,
  "pipeline_default": "simple_fill",
  "wavespeed_key_ready": true,
  "image_api_ready": true,
  "image_model": "gpt-image-2"
}
```

`ready=true` 只表示密钥已载入，不会产生付费调用。

## 六、真实端到端验收

1. 上传一张测试图片并创建项目。
2. 输入一个明确目标，例如“人物手中的书”。
3. 点击“识别并预览修改范围”。看到绿色蒙版说明 SAM3 调用成功。
4. 输入“把书替换成一只白色长毛猫，保持人物和背景不变”。
5. 点击生成。版本区出现结果说明图像编辑接口、裁剪回填和项目持久化全部成功。
6. 打开任务目录中的 `run.json`，确认：

```json
{"outside_edit_mask_changed_pixels": 0}
```

这表示最终有效编辑区域之外的像素没有被改变。

## 七、运行测试

测试不会调用真实 API，也不会产生费用：

```bash
pip install -r requirements-dev.txt
pytest
```

覆盖内容包括：mask alpha 语义、编辑范围扩张、蒙版外像素保护，以及完整裁剪—生成替身—回填链路。

## 八、不要这样配置

- 不要把真实密钥写进 `app/config.py`。
- 不要把 `.env`、客户原图或 `data/projects/` 提交到 Git。
- 不要把 SSH 私钥放进项目目录。
- 不要在前端 JavaScript 中调用供应商接口；浏览器不应接触 API Key。
- 不要把只支持“参考图生成”、但不支持原生 alpha mask 的接口当作兼容接口。
