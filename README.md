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

本地运行时，所有 `local_script` 动作都直接使用本工程文件。`sing-box(fsr)` 使用
`scripts/fscarmen-sing-box.sh`，不需要从 fscarmen 下载入口脚本。涉及安装、网络、内核、服务、
证书或防火墙的动作应以 root 运行；fscarmen 菜单后续下载依赖时仍需要网络。

## VPS 启动

GitHub 仓库：`https://github.com/dandan8511/dandan-tui`

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")
```

只有 wget 时：

```bash
bash <(wget -qO- "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")
```

`launch.sh` 每次从 `main` 下载 TUI 本体、菜单配置、本地 TCP 脚本及 fscarmen 的本地快照到
`${XDG_CACHE_HOME:-~/.cache}/dandan-tui`，然后启动。要固定某个版本：

```bash
YJL_TUI_REF=提交SHA bash <(curl -fsSL "https://raw.githubusercontent.com/dandan8511/dandan-tui/main/launch.sh?v=$(date +%s)")
```

需要 `bash` 和 `python3`；在线安装或检测按动作需要 `curl`、`wget`、`openssl`、`iproute2` 等工具。

## sing-box(fsr)

该分类右侧的完整菜单对应：

```bash
bash <(wget -qO- https://raw.githubusercontent.com/fscarmen/sing-box/main/sing-box.sh)
```

但 TUI 实际执行仓库内完整克隆 [fscarmen-sing-box.sh](scripts/fscarmen-sing-box.sh)。当前快照来源：

```text
upstream commit: e1f08cff8a39ec0ac595d549e886b0ac88514b68
script version:  v1.3.20 (2026.08.07)
sha256:          0fbccc6f4ac6a0b2fa5c7cf90130904eae3f322e2451062dd93d6b6f92d0287f
```

因此上游脚本入口失效后，本项目仍可运行完整菜单。它不是携带所有二进制的断网安装包：系统依赖、
sing-box、cloudflared、证书和订阅模板仍由上游流程联网获取。

### 更新快照

先下载候选版本并检查，再覆盖本地文件：

```bash
git ls-remote https://github.com/fscarmen/sing-box.git refs/heads/main
curl -fL https://raw.githubusercontent.com/fscarmen/sing-box/main/sing-box.sh -o /tmp/fscarmen-sing-box.sh
bash -n /tmp/fscarmen-sing-box.sh
sha256sum /tmp/fscarmen-sing-box.sh
diff -u scripts/fscarmen-sing-box.sh /tmp/fscarmen-sing-box.sh | less
cp /tmp/fscarmen-sing-box.sh scripts/fscarmen-sing-box.sh
```

更新后，把新的提交号、版本和 SHA-256 写回本节，然后运行：

```bash
python3 -m unittest discover -s tests -v
./run.sh --check
git add scripts/fscarmen-sing-box.sh README.md
git commit -m "chore: refresh fscarmen sing-box snapshot"
git push
```

菜单、`launch.sh` 和测试均固定引用 `scripts/fscarmen-sing-box.sh`，正常上游更新不需要再改 TUI 代码。

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
