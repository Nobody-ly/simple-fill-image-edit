# 快速上手

## 1. 准备服务

Simple Fill 需要两项外部能力：

1. WaveSpeed SAM3：把“书”“眼镜”“花瓶”等语义描述转成二值蒙版。
2. 原生蒙版图像编辑接口：接受 `image`、带 alpha 的 `mask` 和 `prompt`，返回 PNG。

第二项采用 OpenAI-compatible `POST /images/edits` 形状。兼容服务需要支持任意或当前裁剪尺寸，并遵循“mask 透明区域可编辑”的语义。

## 2. 安装

Windows PowerShell：

```powershell
git clone https://github.com/Nobody-ly/simple-fill-image-edit.git
Set-Location simple-fill-image-edit
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux：

```bash
git clone https://github.com/Nobody-ly/simple-fill-image-edit.git
cd simple-fill-image-edit
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，至少设置：

```dotenv
IMAGE_API_KEY=replace_me
WAVESPEED_API_KEY=replace_me
```

## 3. 启动

```bash
python run.py
```

健康检查：

```bash
curl http://127.0.0.1:7862/api/health
```

两项应为 `true`：

```json
{
  "wavespeed_key_ready": true,
  "image_api_ready": true
}
```

## 4. 做第一次局部修改

1. 上传一张包含清晰主体的图片。
2. 输入原对象名称，优先用“位置 + 对象”，如“右侧人物手中的书”。
3. 预览蒙版。若漏选，换更准确的语义重新分割；不要直接靠扩大边界弥补大面积漏选。
4. 结果描述只写新内容、关系和必须保持的关键点。
5. 点击生成并等待任务完成。

推荐的第一条提示词：

```text
把人物手中的书完整替换成一只被双手自然抱住的白色长毛猫；保持人物、手臂、衣服、背景、画风、构图和光照不变。
```

## 5. 查看中间文件

每次任务会保存在：

```text
data/projects/<project_id>/tasks/<task_id>/
```

主要文件：

- `target-mask.png`：SAM3 原始目标蒙版。
- `edit-mask.png`：外扩后的最终可编辑区域。
- `inpaint-anything-crop.png`：交给图像模型的局部图。
- `image-edit-alpha-mask.png`：透明处可编辑的原生 alpha mask。
- `image-edit-provider-original.png`：供应商原始返回。
- `candidate-full.png`：回填到完整画布、尚未最终羽化的候选。
- `result.png`：最终结果。
- `run.json`：参数、供应商记录和范围外像素校验。
