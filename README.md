# YJL Linux TUI

这是一个基于 Python curses 的 Linux 终端管理工具。菜单和动作来自 scripts.json，
以后新增脚本时主要修改 JSON，不需要重写菜单。

## VPS 使用

GitHub 仓库地址：

    https://github.com/dandan8511/dandan-tui

在 Debian 11/12/13、主流 Ubuntu 或 Alpine VPS 上，先以 root 或具有 sudo 权限的用户登录，
然后执行下面的一条命令即可启动最新 TUI：

    bash <(curl -fsSL "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")

如果系统只有 wget：

    bash <(wget -qO- "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")

`launch.sh` 会从 GitHub 下载当前 `main` 分支的 `run.sh`、`tui.py` 和 `scripts.json`，
保存到 VPS 的 `${XDG_CACHE_HOME:-~/.cache}/dandan-tui`，然后启动 TUI。每次执行都会重新
下载最新文件，不需要先 git clone，也不会把整个仓库下载到 VPS。

root 用户推荐这样启动：

    sudo -i
    bash <(curl -fsSL "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")

直接执行 GitHub `main` 分支代表信任仓库当前内容。如果希望固定到某一次提交，可以指定
提交 SHA，而不是跟随 `main`：

    YJL_TUI_REF=提交SHA bash <(curl -fsSL "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")

TUI 运行需要 `bash` 和 `python3`；在线安装、证书和部分检测功能还需要 `curl`、`wget`、
`openssl`、`iproute2` 或系统对应的工具。涉及内核、网络、GRUB、Nginx、证书和面板安装的
功能需要 root 权限。首次启动会显示一次欢迎语，之后直接进入菜单。

启动：

    cd /home/yjl/yjl-tui-jiaoben/linux-tui
    ./run.sh

无交互检查：

    ./run.sh --check
    ./run.sh --list

现有脚本的对应关系：旧版 dandan VPS 自动发现仓库中的 vps.sh，不复制、不修改原脚本；
Swap 512MB 内置动作先检查活动 Swap，有则跳过；fnm 动作安装并切换 Node 22.11.0；
sing-box 以 -L 执行；Check.Place 以 -I 执行；SSH 动作隐藏读取密码，修改前备份，
用 sshd -t 校验，重载后检查端口，失败回滚。

在线安装分类还包含 3x-ui、bin456789 DD 重装、233boy sing-box、233boy Xray、宝塔、1Panel
和 CasaOS。网络与内核分类包含官方内核管理、fscarmen sing-box 菜单中的完整 TCP Brutal /
内核工具入口、网卡配置和 IPv4/IPv6 出站优先级；网卡管理会自动检测 NetworkManager、
netplan、systemd-networkd、ifupdown 或 Alpine OpenRC，不要求用户判断后台服务；TCP 入口的
实际来源是 fscarmen 菜单调用的 Linux-NetSpeed `tcp.sh`，菜单使用“（fsc）”标记来源。

服务器配置分类包含 apt/apk 系统更新、SSH、Swap 和小硬盘日志策略。高级工具包含 Nginx
SSL 证书管理、WebDAV、nft-forward、port-traffic-dog 和 GRUB 管理。SSL 使用 acme.sh，
可从 Nginx `server_name` 扫描域名，支持 HTTP-01 和 Cloudflare/阿里云/腾讯云 DNS-01；
申请前会做 DNS 和本机监听检查，申请后备份 Nginx 配置、安装证书并校验 `nginx -t`。

首次启动会显示一次“YJL 专用脚本”欢迎语，标记保存在 root 的 `/var/lib/yjl-tui` 或普通用户
的 `~/.local/state/yjl-tui`。复杂的网络、内核、GRUB 和证书动作都需要 root，并且会在修改
前备份；远程网络修改和内核/GRUB 操作仍应在真实目标系统上验证。

网络与内核分类的“SNI优选域名延迟测试”使用 443 + SNI 握手，每个域名超时 1 秒，结果写入
TUI 日志；它是连通性/握手耗时参考，不等同于 HTTP 下载速度。
时间戳使用 13 位毫秒值，兼容 Debian 上把百分号 3N 当成完整纳秒输出的 date 实现，避免出现负数或超大延迟。

在线脚本会先下载到缓存文件，再做 bash -n 或 sh -n 语法检查，之后才执行；不会使用
bash <(curl ...) 或管道直执行。交互脚本在真实终端运行，并通过 util-linux 的 script
保存回显。安全动作直接执行；修改系统的动作需要 y/N；高风险动作需要输入 RUN。

默认缓存和日志：

    root：/var/cache/yjl-tui       /var/log/yjl-tui
    普通用户：~/.cache/yjl-tui    ~/.local/state/yjl-tui

可覆盖：

    YJL_TUI_CACHE_DIR=/tmp/yjl-cache YJL_TUI_LOG_DIR=/tmp/yjl-logs ./run.sh

在 scripts.json 的 actions 数组添加 kind 为 builtin、online、legacy 或 custom 的动作。
online 至少填写 url、interpreter、args 和 risk；legacy 会按 basename 在工作区查找本地脚本。
