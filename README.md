# CatsCo Inpaint Anything · 封装前完整多分支版本

这是 `simple-fill-image-edit` 在简化封装之前的完整实验实现，保留三条真实运行分支：

- `simple_fill`：语义/空间蒙版 → 安全生成范围 → Image2 原生 mask → 完整候选回填。
- `legacy / fill_anything`：可对生成结果再次 SAM3，再构造语义提交区域。
- `object_v2`：Big-LaMa clean plate、前景保护、结果对象分割、trimap/alpha 合成。

当前分支不包含历史运行数据、临时文件、API Key、SSH 私钥和模型权重。服务 URL 与配置字段保留。

## 默认地址

`http://127.0.0.1:7862/`

如果 7862 已被 `main` 简化版占用：

```powershell
$env:CATSCO_INPAINT_PORT="7863"
python run.py
```

然后打开 `http://127.0.0.1:7863/`。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

项目不会自动读取 `.env`，应通过 PowerShell、系统服务或私密启动脚本设置变量；不要把真实 Key 写进 Git。

## 最小配置

```powershell
$env:WAVESPEED_API_KEY="<set-locally>"
$env:CATSCO_IMAGE2_HOST="203.32.85.223"
$env:CATSCO_IMAGE2_USER="root"
$env:CATSCO_IMAGE2_SSH_KEY="$HOME\.ssh\worker2_203_32_85_223_ed25519_v2"
```

也可以配置直接 OpenAI-compatible mask 路线：

```powershell
$env:CATSCO_MASKED_IMAGE2_BASE_URL="https://app.catsco.cc/v1"
$env:CATSCO_MASKED_IMAGE2_API_KEY="<set-locally>"
$env:CATSCO_MASKED_IMAGE2_MODEL="gpt-image-2"
```

## Inpaint Anything 与 Big-LaMa

默认上游路径保留历史机器配置：

`D:\codex_workspace\catsco-inpaint-anything-2026-upstream`

迁移时设置：

```powershell
$env:INPAINT_ANYTHING_UPSTREAM="<Inpaint Anything 上游绝对路径>"
```

`simple_fill` 不要求 Big-LaMa；`remove` 和 `object_v2` 需要完整 GPU Big-LaMa 权重。

`requirements.txt` 保留封装前内容，并不单独锁定 CUDA 版 PyTorch；应先按目标显卡/CUDA 安装匹配的 `torch`，再安装其余依赖。缺少 `torch` 时首页仍可能打开，但 `/api/health` 会在 LaMa 安装检查阶段返回 500。

## 启动与检查

```powershell
python run.py
Invoke-RestMethod http://127.0.0.1:7862/api/health
```

页面标题应为 `CatsCo 语义局部重绘 · Fill Anything`。

## 推荐首次测试

1. 上传图片。
2. 用短英文语义如 `book`、`glasses` 或 `vase` 生成 SAM3 蒙版。
3. 检查预览，拒绝空蒙版、全图蒙版和漏选。
4. 普通对象替换先选 `simple_fill`。
5. 基线：dilation 6、growth ratio 0.35、feather 3。
6. 查看 provider 原始图、commit mask 和最终结果。
7. 超时先检查远端固定任务目录，不立即重复付费。

详见 [docs/PRE_PACKAGE_HANDOFF.md](docs/PRE_PACKAGE_HANDOFF.md)。
