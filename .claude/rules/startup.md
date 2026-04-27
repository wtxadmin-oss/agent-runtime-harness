# 项目启动命令

## 前置（首次或权限异常时）

```bash
sudo chown -R vscode:vscode ~/.nanobot/
nanobot onboard
```

## 终端 1：后端 Gateway

```bash
set -a && source /workspaces/nanobot/.env && set +a
nanobot gateway
```

## 终端 2：前端 WebUI

```bash
cd /workspaces/nanobot/webui
bun run dev
```

访问：http://localhost:5173
