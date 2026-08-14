# YJL Linux TUI

面向 Linux VPS 的终端管理菜单。菜单定义在 `scripts.json`，本地脚本在 `scripts/`，主程序为 `tui.py`。

## 本地运行

在项目目录直接运行：

```bash
./run.sh
```

无交互检查：

```bash
./run.sh --check
./run.sh --list
```

本地运行时，所有 `local_script` 动作都直接使用本工程文件。`sing-box(fsr)` 与 `WARP 管理菜单（本地克隆）`
分别使用 `scripts/fscarmen-sing-box.sh`、`scripts/fscarmen-warp.sh`，不需要从 fscarmen 下载入口脚本。涉及
安装、网络、内核、服务、证书或防火墙的动作应以 root 运行；fscarmen 菜单后续下载依赖时仍需要网络。

## Nginx 管理工具

“服务器配置”分类中的“Nginx 管理工具”只管理当前 VPS。本机每次进入都会实时执行
`nginx -t`、`nginx -T`、`ss -lntup` 和 `systemctl is-active nginx`，先展示本机域名、站点配置文件、
监听端口、静态目录、反代上游、证书和服务状态。

菜单支持刷新扫描、查看站点原始配置、新建反向代理站点、新建静态站点，以及证书申请和自动续期。创建新站点时会拒绝
已监听的 TCP 端口，先把临时 `.conf` 放入 `/etc/nginx/conf.d/` 参与 `nginx -t`，通过后才原子替换并
reload；失败会把新文件移入 `/var/backups/yjl-tui/nginx/`，不覆盖现有业务。

证书统一使用本机 `certbot`：申请成功后启用 `certbot.timer`，并执行 `certbot renew --dry-run` 验证自动
续期链路。HTTP-01 要求域名公网 TCP 80 可访问；Cloudflare DNS-01 需要安装
`python3-certbot-dns-cloudflare`，Token 会写入 `/etc/letsencrypt/yjl-tui/` 下的 `0600` 凭据文件，供
后续定时续期使用。Token 不写入 TUI 日志。

同一分类中的“nginx 网页管理工具 (nginx-ui)”是可选网页面板。`launch.sh` 不会下载 nginx-ui；只有选择
该菜单项时，TUI 才会从 `dandan8511/nginx-ui` 下载安装器。你的镜像 Release
`v2.5.7` 已包含 Linux x64、x32 和 arm64 三个二进制附件；安装器会按当前架构从你的 Release 下载对应
文件，并新建 nginx-ui 服务和网页监听端口，不能把它当作已有 Nginx 的无影响更新。

## VPS 启动

GitHub 仓库：`https://github.com/dandan8511/dandan-tui`

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")
```

只有 wget 时：

```bash
bash <(wget -qO- "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")
```

`launch.sh` 每次从 `main` 下载 TUI 本体、菜单配置、本地 TCP 脚本、Docker 镜像源检测脚本，以及 fscarmen、tcpfit、nft-forward 的本地快照到
`${XDG_CACHE_HOME:-~/.cache}/dandan-tui`，然后启动。要固定某个版本：

```bash
YJL_TUI_REF=提交SHA bash <(curl -fsSL "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")
```

需要 `bash` 和 `python3`；在线安装或检测按动作需要 `curl`、`wget`、`openssl`、`iproute2` 等工具。

## 猴哥 nft-forward 本地副本

高级工具中的“猴哥 nft-forward 端口转发（本地工具包）”执行仓库内
`tools/nft-forward/install.sh`。同一目录固定包含 Release `v0.68.0` 的 `nft-agent`、
`nft-server` 和 `SHA256SUMS`；本地克隆运行 TUI 时直接使用该目录。`launch.sh` 启动时仅下载
约 37 KB 的安装器；只有进入该菜单、且缓存内没有工具包时，安装器才从本仓库下载二进制和
`SHA256SUMS` 到缓存目录。默认安装会通过 `file://` 从该工具目录读取发布物并按校验文件验证，
不需要访问上游 install.sh 或该版本的 GitHub Release。

工具包的原始安装器快照来自 `xjetry/nft-forward` 的提交
`5c099fdd6000dbfb088387c8494fbbbfb1de5025`，其 Release `v0.68.0` 的 `nft-agent`、
`nft-server`、`SHA256SUMS` 已逐项校验并随本仓库保存。脚本的 `update-script` 和安装后生成的
`nft-forward-upgrade` 会从 `dandan8511/dandan-tui/tools/nft-forward/install.sh` 更新自身，
而非上游的 `install.sh`。要升级到未打包的新 Release，可显式传入 `--release <tag>`；这时才会
从上游或通过 `NFTF_RELEASE_BASE_URL` 指定的发布源下载对应二进制。

## Docker 镜像源检测

Docker 管理分类的 `16. 国内 Docker 源检测` 运行仓库内
[`scripts/docker-mirror-switch.sh`](scripts/docker-mirror-switch.sh)。默认的交互流程仅先检查候选源
能否完成 Docker Registry v2 鉴权和 manifest 下载；后续真实拉取、写入 Docker 配置、重启服务均会再次
询问确认。脚本只接受 Docker Hub 测试镜像，且只会改写 `/etc/docker/daemon.json` 的
`registry-mirrors` 字段，原文件会在应用前按时间戳备份。

在 TUI 外也可以直接运行：

```bash
# 仅检查 Registry API，不拉镜像、不改配置
sudo bash scripts/docker-mirror-switch.sh --check

# 真实拉取验证，但不改配置
sudo bash scripts/docker-mirror-switch.sh --verify-pull

# 验证后备份、写入镜像源并重启 Docker
sudo bash scripts/docker-mirror-switch.sh --apply
```

单个真实拉取默认 90 秒超时，拉取通过的测试标签会自动删除。`registry-mirrors` 只加速 Docker Hub
引用，例如 `alpine`、`nginx`、`mysql:5.7`；它不会代理明确写成 `ghcr.io/...` 或 `quay.io/...` 的镜像。

## Docker Hub Mirror 服务端

Docker 管理分类的 `17. 本机部署 Docker Hub Mirror` 部署官方 `registry:2` 的
pull-through cache。它适合放在国外 OVH 服务器：第一次拉取由 OVH 访问 Docker Hub，后续国内机器
从 OVH 读取已缓存层。脚本会在 `10305-10307` 中检查 TCP 和 UDP 后自动选择空闲端口，也可以用
`--port` 指定；缓存默认保存在 `/var/lib/dockerhub-mirror/data`。

服务端先准备域名和 HTTPS（或仅限源站 IP 的防火墙规则），然后执行：

```bash
sudo bash scripts/dockerhub-mirror.sh --server --port 10305 --public-url https://mirror.example.com
```

没有域名时可以临时使用 HTTP，但必须在国内客户端显式加 `--insecure`，例如：

```bash
sudo bash scripts/dockerhub-mirror.sh --client --mirror http://OVH_IP:10305 --insecure
```

HTTP 不加密且容易被滥用，建议只用于验证；公网长期运行应使用 HTTPS，并在 OVH 防火墙只放行
国内服务器 IP。服务端 `--status` 查看容器，`--uninstall` 只删除容器而保留缓存数据。Mirror 仅
处理 Docker Hub 引用，不会代理 `ghcr.io`、`quay.io` 等其他 Registry。

### 缓存管理

17 号菜单还提供缓存管理：可列出已缓存仓库、选择删除例如 `library/redis`，停止 Mirror 后运行
Registry 垃圾回收，再启动 Mirror。删除只影响 OVH 缓存，不影响任何客户端已拉取的镜像。缓存总目录是
`/var/lib/dockerhub-mirror/data`。自动策略按每天 04:25 执行：可设缓存总限额和保留天数；达到任一条件
时会短暂停止 Mirror、清空**整库**缓存、再重新启动。官方 pull-through cache 没有安全的按镜像 LRU 或
按镜像过期机制，所以这里明确使用整库轮换，不会假装只删除某个“最旧镜像”。

命令行也可使用：

```bash
# 查看缓存仓库和总占用
sudo bash scripts/dockerhub-mirror.sh --cache-list

# 删除 Redis 缓存仓库并回收无引用 layer
sudo bash scripts/dockerhub-mirror.sh --delete-repository library/redis

# 设为超过 1 GiB 或超过 14 天时，定时清空全部 Mirror 缓存
sudo bash scripts/dockerhub-mirror.sh --configure-policy --max-cache-gb 1 --expire-days 14
```

## fscarmen sing-box 与 WARP 本地快照

这两个菜单的实现是相同的：把上游**入口脚本完整保存**到本仓库，TUI 以 `local_script` 执行；`launch.sh`
又会把快照同步到 VPS 的 `${XDG_CACHE_HOME:-~/.cache}/dandan-tui/scripts/`。所以本地运行和 GitHub
一行启动都先使用你的 GitHub 仓库文件，而不是当场下载上游入口。

这只本地化入口，不是离线安装包。菜单继续安装时要下载系统包、sing-box 核心、WireGuard、wireproxy、
cloudflared、证书或订阅模板，仍需要网络，且这些运行依赖仍由各自上游提供。

### sing-box(fsr)

该完整菜单对应：

```bash
bash <(wget -qO- https://raw.githubusercontent.com/fscarmen/sing-box/main/sing-box.sh)
```

但 TUI 实际执行仓库内完整克隆 [fscarmen-sing-box.sh](scripts/fscarmen-sing-box.sh)。当前快照来源：

```text
upstream commit: 10ee5cfbbb463aaf6e5a9de6bf3cf5c9333df579
script version:  v1.3.22 (2026.08.11)
sha256:          6e964563045c094fe2b9db855e2c921134cea6386644e9ec833b8feb0357f3a6
```

### WARP

该完整菜单对应：

```bash
wget -N https://gitlab.com/fscarmen/warp/-/raw/main/menu.sh && bash menu.sh
```

TUI 实际运行 [fscarmen-warp.sh](scripts/fscarmen-warp.sh)。当前快照来源：

```text
upstream commit: 3f7e4529714b7e634f05ac9f5c2efd41608f9211
script version:  3.2.7
sha256:          51a73716f23dcca716bc81083d5d50f817693550c6882107252daf5b35eb7c13
```

WARP 本身有“同步脚本至最新版本”菜单项。上游原实现会再下载 GitLab 的 `menu.sh`；本项目仅对此处做了
定向，将它改为从 `dandan8511/dandan-tui` 的 GitHub Raw 下载 `scripts/fscarmen-warp.sh`。也就是说，
从 TUI 启动 WARP 后在 WARP 菜单里升级，仍会保持使用你的本地化版本；可临时用 `YJL_WARP_UPDATE_URL`
覆盖该地址测试分支。

### 后续更新约定

以后只要提出“看看 README，更新工程的 sing-box 和 WARP 脚本”，按下面流程执行：先分别取上游 `main`
候选文件，记录提交号、版本、SHA-256 和 `diff`；语法检查通过后才用完整候选文件覆盖本地快照。WARP 覆盖后必须
重新应用上面的 `YJL_WARP_UPDATE_URL` 与 `ver()` 下载地址两处定制，不能把它们带回 GitLab。sing-box 当前没有
本项目私有补丁，应保持逐字上游完整快照。

操作命令如下。临时文件只用于对比，不直接在服务器执行：

```bash
git ls-remote https://github.com/fscarmen/sing-box.git refs/heads/main
curl -fsSL https://raw.githubusercontent.com/fscarmen/sing-box/main/sing-box.sh -o /tmp/fscarmen-sing-box.sh

git ls-remote https://gitlab.com/fscarmen/warp.git refs/heads/main
curl -fsSL https://gitlab.com/fscarmen/warp/-/raw/main/menu.sh -o /tmp/fscarmen-warp.sh

bash -n /tmp/fscarmen-sing-box.sh
bash -n /tmp/fscarmen-warp.sh
sha256sum /tmp/fscarmen-sing-box.sh /tmp/fscarmen-warp.sh
diff -u scripts/fscarmen-sing-box.sh /tmp/fscarmen-sing-box.sh | less
diff -u scripts/fscarmen-warp.sh /tmp/fscarmen-warp.sh | less
```

确认变更后，完整覆盖 sing-box；完整覆盖 WARP 后再加回本仓库升级地址：

```bash
cp /tmp/fscarmen-sing-box.sh scripts/fscarmen-sing-box.sh
cp /tmp/fscarmen-warp.sh scripts/fscarmen-warp.sh
# 重新加入 YJL_WARP_UPDATE_URL 变量，并把 ver() 的下载地址改为 "$YJL_WARP_UPDATE_URL"
```

最后把新的提交号、版本和 SHA-256 写回本节，并执行：

```bash
python3 -m unittest discover -s tests -v
./run.sh --check
git add scripts/fscarmen-sing-box.sh scripts/fscarmen-warp.sh README.md tests/test_smoke.py
git commit -m "chore: refresh fscarmen script snapshots"
git push
```

正常的上游脚本更新不需要改 `tui.py`、`scripts.json` 或 `launch.sh`，因为它们已固定引用两份本地快照；
但每次均须保留并运行针对两份快照的语法和冒烟测试。

## tcpfit 实测 TCP 调优

TCP 调优分类的 `99. tcpfit 实测 TCP 调优（本地副本）` 对应
[Kylin010/tcpfit](https://github.com/Kylin010/tcpfit)。完整上游快照保存在
`scripts/tcpfit/`，TUI 实际执行 `scripts/tcpfit/tcpfit.sh`，不使用在线管道脚本。
本次固定的来源为：

```text
upstream commit: 3e285932e5f212eef9be9591ebba9a78a3b4d1c7
upstream date:   2026-08-10
script version:  0.5.3
sha256:          6c86b31c3d937736bb4d919b04b732013cbb2da958d97841b34cd663fd2a6b35
license:         MIT (Kylin010)
```

它根据带宽、BDP 和可选的 `iperf3` 实测推导参数。调优会写入 sysctl、队列规则与 systemd
配置，因此菜单要求 root 且属于高风险动作；首次修改前会保存快照，原菜单提供 `rollback` 回滚。
远端使用 `launch.sh` 时只会下载执行所需的 `tcpfit.sh`，而 GitHub 仓库保留完整上游快照、许可证、
安装器和多机编排示例。

### 更新快照

先确认候选提交与语法，再从临时克隆导出工作树（不要把嵌套 `.git` 目录提交进本仓库）：

```bash
git clone --depth 1 https://github.com/Kylin010/tcpfit.git /tmp/tcpfit
git -C /tmp/tcpfit log -1 --format='%H %cs %s'
bash -n /tmp/tcpfit/tcpfit.sh
sha256sum /tmp/tcpfit/tcpfit.sh
git -C /tmp/tcpfit archive --format=tar HEAD | tar -xf - -C scripts/tcpfit
python3 -m unittest discover -s tests -v
```

## 运行数据

```text
root:     /var/lib/yjl-tui  /var/log/yjl-tui  /var/cache/yjl-tui
普通用户: ~/.local/state/yjl-tui              ~/.cache/yjl-tui
```

可临时覆盖：

```bash
YJL_TUI_CACHE_DIR=/tmp/yjl-cache YJL_TUI_LOG_DIR=/tmp/yjl-logs ./run.sh
```

TUI 不显示风险标签，也不在执行前要求确认；上游脚本自身的交互保持原样。
