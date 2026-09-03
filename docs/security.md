# 安全与发布检查

## 仓库刻意不包含

- 真实 API Key、Cookie、Bearer Token。
- SSH 私钥、公钥配置、服务器地址或账号。
- 私有网关 URL、内部 Bot Token 或长期签名下载地址。
- 用户原图、蒙版、生成结果和项目 manifest。

## 密钥处理

- 本地开发：只写入 `.env`；该文件已忽略。
- CI / 生产：使用 GitHub Actions Secrets、云 Secret Manager 或部署平台 Secret。
- 浏览器永远只访问本应用后端；后端不会在健康检查或任务 JSON 中返回完整密钥。
- 供应商记录只保存模型名、请求 ID、endpoint host 等非凭据信息。

## 数据风险

上传图会发送给配置的 SAM3 和图像编辑服务。使用真实客户素材前，应确认数据保留、训练使用、地区合规和删除政策。公开部署还必须增加认证和租户隔离；当前文件存储面向单机可信环境。

## 发布前检查

```bash
git grep -n -E "(wsk_live_|sk-[A-Za-z0-9_-]{20,}|BEGIN .*PRIVATE KEY|Authorization: Bearer)" || true
git status --short
pytest
```

若曾经误提交密钥，仅从最新文件删除是不够的：应立即吊销密钥，并使用历史重写工具清理 Git 历史。
