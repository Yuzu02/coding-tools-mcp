# MCP 桌面客户端

这是 `coding-tools-mcp` 的 Python 桌面客户端 MVP，核心目标是让研发同学用一个中文界面完成：

- 管理多个 Workspace
- 配置公网暴露地址，当前支持 FRP 和 Cloudflare
- 配置 OAuth / Bearer / NoAuth
- 启动和停止本地 MCP 运行时
- 查看运行日志和当前入口地址
- 直接复制 ChatGPT 自定义 MCP 应用需要填写的核心字段

## 运行

```bash
python apps/desktop-client/main.py
```

## 依赖

- Python 3.11+
- PySide6
- psutil
- `uvx` 或 `coding-tools-mcp` 已在 PATH 中可用

## ChatGPT 接入

当认证方式选择 `oauth` 后，界面里会直接展示并支持复制：

- 连接地址
- OAuth 客户端 ID
- OAuth 客户端密钥
- 授权口令

如果你使用 FRP，只需要把 Workspace、本地端口、FRP 子域名和服务器域名配好，再启动运行时即可。

如果你使用 Cloudflare，有两种模式：

- 临时隧道：使用 `cloudflared tunnel --url`，启动后自动分配一个 `trycloudflare.com` 公网地址
- 固定域名：使用 `Tunnel Token` 启动命名隧道，并在界面里填写固定公网地址

## 当前限制

- 当前只接通了 FRP 和 Cloudflare
- `Ngrok`、`Dev Tunnel` 还没有实现真实隧道启动能力
- Cloudflare 命名隧道模式依赖你提前在 Cloudflare 仪表盘里配置好 tunnel 和 hostname
- Cloudflare 命名隧道模式下，本地服务地址需要和 Cloudflare Tunnel 的 ingress 目标一致，通常是 `http://127.0.0.1:<本地端口>`
