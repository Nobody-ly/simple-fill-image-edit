# 快速上手

## 1. 准备服务

Simple Fill 需要两项外部能力：

1. WaveSpeed SAM3（可选）：把“书”“眼镜”“花瓶”等语义描述或框提示转成贴合物体的二值蒙版。直接框选模式不依赖它。
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

图像编辑服务必须为 `true`；只有使用“SAM3 智能贴边/语义识别”时，WaveSpeed 才必须为 `true`：

```json
{
  "wavespeed_key_ready": true,
  "image_api_ready": true
}
```

## 4. 做第一次局部修改

1. 上传一张包含清晰主体的图片。
2. 直接在中间图片上拖动矩形，覆盖旧对象并给新对象留出需要的空间。
3. 点击“直接使用框选”并预览；若希望蒙版紧贴物体轮廓，再选择“SAM3 智能贴边”。若智能贴边漏选，优先改用完整矩形，不要让识别结果阻断编辑。
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
