# SSH 专用连接

适用于电脑所在网络可以正常同步、服务器直连失败的情况。电脑需保持开机、联网且不休眠。

此方案只转发官方授权和取数请求，不更改服务器默认路由或现有代理规则，不影响水鱼、落雪等请求。转发过程不解密、不记录请求正文，插件继续校验官方 HTTPS 证书。

## 准备

- 电脑已有 Python 3.10+、OpenSSH 客户端及可登录服务器的私钥。
- 先正常登录一次服务器，核对并保存主机指纹；工具不会跳过主机校验。
- 将本插件的 `tools/official_ssh_tunnel.py` 同步到服务器，普通 SSH 用户需有读取权限。
- 确认 AstrBot 容器 IP、Docker 网关地址及两个未占用端口。下列地址仅为示例；容器重建后应重新核对。

## 电脑启动

推荐在服务器 AstrBot 的 **舞萌管理 → 更多管理 → SSH 隧道** 页面生成脚本：核对检测到的容器网关与来源地址，填写自己的 SSH 用户、服务器地址及 SSH 端口，点击「生成连接脚本」后复制到 Windows 电脑的 PowerShell 运行。非 Linux 页面会提示手动填写目标 Linux 服务器地址；宿主机网络、特殊容器网络或自动检测失败时也应手动核对。

生成脚本自带连接工具，电脑无需安装插件；运行时询问私钥路径（默认 `~/.ssh/id_ed25519`）和 Python 可执行文件（默认 `python`），并检查电脑的 Python 及服务器的 `python3` 均为3.10+。脚本通过 SSH/SCP 将辅助工具安装到服务器 SSH 用户的 `.cache/maimai-ssh/`，在电脑的 `~/.maimai-ssh/` 保存工具与连接状态；不读取或复制私钥内容。有口令的私钥请先加入 `ssh-agent`，后续重连需要无交互认证。首次连接须核对主机指纹，不跳过证书或主机验证。

页面只提供脚本与待填的代理地址，不会执行脚本、修改 AstrBot 配置、改变现有代理规则或安装开机自启。保持 PowerShell 窗口开启，按 Ctrl+C 停止；关闭电脑或窗口后通道不可用。重新生成脚本前先停止旧通道；容器重建或 IP 变化后重新检测、生成并更新代理地址。

也可按以下方式手动启动已有工具：

在插件目录执行，替换服务器账号、IP、密钥及服务器工具路径：

```powershell
python tools/official_ssh_tunnel.py connect `
  --server user@server.example `
  --identity "$HOME/.ssh/id_ed25519" `
  --remote-tool /path/to/plugin/tools/official_ssh_tunnel.py `
  --bridge 172.18.0.1 --bridge-port 17890 --remote-port 17891 `
  --allow-source 172.18.0.2 `
  --status-file "$HOME/maimai-ssh/status.json"
```

电脑入口只监听本机回环地址；服务器 SSH 转发也只监听回环地址。辅助进程在指定 Docker 网关上提供入口，并只允许指定容器 IP 访问，不需要调整 SSH 的 `GatewayPorts` 或开放公网端口。

工具会自动尝试重连。`status.json` 仅记录连接状态和进程号，不包含登录凭据；`connected` 只代表通道建立，不代表官方接口已验证可用。当前工具不自动安装开机启动任务。

## 验证与接入

在 AstrBot 容器内，使用同一 Python 环境运行插件的检测工具：

```sh
python tools/setup_official_runtime.py --network-check --proxy http://172.18.0.1:17890
```

检测通过后，在插件配置「官方成绩同步」中填写「官方取数专用代理」为 `http://172.18.0.1:17890`，并重载本插件。留空恢复直连。不要将服务器的公网 IP 或容器内的 `127.0.0.1` 当成 Docker 网关地址。

电脑断网、休眠或关闭工具时，同步会报错，不会自动切回其他出口。连接状态不稳定时，请先恢复连接再提交新凭据。

## 停止

前台运行时按 Ctrl+C。后台运行时，在电脑执行：

```powershell
python tools/official_ssh_tunnel.py stop --status-file "$HOME/maimai-ssh/status.json"
```

工具会关闭 SSH 连接，服务器辅助进程随之退出，不留下持续监听的入口。
