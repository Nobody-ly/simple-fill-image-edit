# Simple Fill Image Edit

[![tests](https://github.com/Nobody-ly/simple-fill-image-edit/actions/workflows/tests.yml/badge.svg)](https://github.com/Nobody-ly/simple-fill-image-edit/actions/workflows/tests.yml)

一个可自托管的局部重绘工作台。用户上传图片后可以直接拖动框选编辑范围，也可选用 SAM3 智能贴合对象边缘，再描述替换内容。提交后，后端按照固定函数链路执行，不需要 Agent 在中间判断或改写参数。

```text
原图 + 用户框选 ─────────────┐
   或                       ├→ 确认后的二值蒙版
原图 + 语义名称 → SAM3 ─────┘
    ↓
边界外扩 + Inpaint Anything 裁剪
    ↓
OpenAI-compatible /images/edits 原生 mask 请求
    ↓
原位回填 + 内向羽化
    ↓
版本、任务与中间文件持久化
```

## 适合什么

- 把海报里的书替换成猫、把普通眼镜替换成星形眼镜。
- 修改单个物体的颜色、材质或样式。
- 替换局部标题或短文案（文字准确性仍取决于图像模型）。
- 需要“蒙版外像素严格不变”、可查看任务历史和重新执行的本地工作流。

它不是 Photoshop 图层拆分工具，也不保证模型在复杂遮挡、极小文字或品牌标志上每次都得到生产级结果。

## 快速开始

要求 Python 3.11+。

```bash
git clone https://github.com/Nobody-ly/simple-fill-image-edit.git
cd simple-fill-image-edit
python -m venv .venv
```

激活虚拟环境后安装依赖：

```bash
pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，填写自己的服务密钥：

```dotenv
IMAGE_API_BASE_URL=https://api.openai.com/v1
IMAGE_API_KEY=your_image_api_key
IMAGE_MODEL=gpt-image-2
WAVESPEED_API_KEY=your_wavespeed_api_key
```

运行：

```bash
python run.py
```

打开 <http://127.0.0.1:7862>。

## 标准操作

1. 上传 PNG、JPG 或 WebP，创建项目。
2. 在中间图片上按住拖动，框住允许模型修改的区域。
3. 点击“直接使用框选”。若需要紧贴物体轮廓，可改用“SAM3 智能贴边”；没有框时仍可仅输入对象名称进行语义识别。
4. 输入结果要求，例如“替换成一只被双手自然抱住的白色长毛猫”。
5. 首次使用保持默认参数，点击生成。
6. 在版本区查看、下载，或基于结果继续编辑；失败任务也会保留并可重试。

生成前的蒙版确认是必要步骤。直接框选不会调用 SAM3，默认在框外预留约 15% 安全生成空间，避免新对象被硬边裁断；智能贴边适合轮廓明确、需要少改周边的物体。蒙版遗漏的旧对象像素不会被模型修改，蒙版过大会给模型过多自由度。

## 固定行为与可调参数

| 项目 | 默认值 | 作用 |
|---|---:|---|
| `dilation` | 6 px | 清掉旧对象轮廓残留 |
| `growth_ratio` | 框选 0.15 / SAM3 0.35 | 为新对象预留安全空间；严格区域可手动选择 0 |
| `feather` | 3 px | 只在蒙版内侧柔化接缝 |
| `crop_size` | 512 px | Inpaint Anything 局部裁剪尺寸 |

系统会验证最终结果在有效编辑蒙版之外改变的像素数为 0，并将验证值写入每次任务的 `run.json`。

## 文档

- [快速上手](docs/quickstart.md)
- [完整链路与边界](docs/architecture.md)
- [配置参考](docs/configuration.md)
- [可直接使用的示例](docs/examples.md)
- [HTTP API](docs/api.md)
- [故障排查](docs/troubleshooting.md)
- [安全与发布检查](docs/security.md)

## 数据与隐私

项目文件默认保存在 `data/projects/`，包括用户原图、蒙版、结果、中间产物和任务记录。该目录已被 `.gitignore` 排除。应用会把输入图上传到配置的 SAM3 与图像编辑服务；部署前请检查供应商的数据政策。

仓库不包含任何真实 API Key、内部网关、SSH 配置、用户图片或历史项目。

## 第三方代码与许可

项目以 Apache-2.0 发布。`third_party/inpaint_anything_mask_processing.py` 基于 [Inpaint Anything](https://github.com/geekyutao/Inpaint-Anything) 的 Apache-2.0 裁剪/回填逻辑，修改说明见 [NOTICE](NOTICE)。
