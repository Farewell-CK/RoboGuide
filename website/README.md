# NAVI 文档网站

本目录为 NAVI 分支提供独立静态文档站，不依赖 main 的架构文档、Rust 工程、
模型权重或运行密钥。编辑 pages/ 下的 Markdown，生成目录 docs/ 和 site/ 不提交。

## 构建与预览

在仓库根目录、Python 3.11 或更新的环境中运行：

    python -m pip install -r website/requirements.txt
    python website/sync_docs.py
    python -m mkdocs build --strict -f website/mkdocs.yml
    python website/check_site.py
    python -m http.server 39802 --bind 127.0.0.1 --directory website/site

安装 uv 的环境可用 uv run --no-project python 运行标准库脚本，用以下命令构建：

    uvx --from mkdocs==1.6.1 --with-requirements website/requirements.txt mkdocs build --strict -f website/mkdocs.yml

sync_docs.py 只处理明确列出的页面，不复制用户数据。build-info.json 记录源提交、
分支、时间和工作区状态；未提交的本地预览会标为 dirty。
check_site.py 检查生成页面、站内链接、锚点及静态资源，不访问外部网络。

## Cloudflare 发布边界

独立站点名为 **roboguide-navi-docs**。原有 **roboguide-docs** 属于主项目文档，
不能用本目录覆盖。发布前需要 Cloudflare 管理员配置独立 Worker：

- Root directory：website。
- Build command：python -m pip install -r requirements.txt && python sync_docs.py && python -m mkdocs build --strict && python check_site.py。
- Production branch：合并文档源码后的 feat/navigation-data-archive。
- Deploy command：npx wrangler deploy；仅用于独立 Worker。
- 原有 roboguide-docs：生产分支保持 main，关闭对 NAVI 分支的构建。

Workers Builds 会覆盖 Wrangler 中的目标名称，不能只靠配置文件的 name 隔离。
sync_docs.py 在 WORKERS_CI=1 时检查 WRANGLER_CI_OVERRIDE_NAME；目标不是
roboguide-navi-docs 或目标未知时，在生成文件前中止。这会明确报错，不会伪造成功。
此保护仅覆盖运行上述同步脚本的构建流程；云端仍需核对实际构建和发布命令。

新增源码解决本分支缺少网站构建输入的问题，**不等于已修复云端历史失败**。
历史 GitHub 检查未附具体报错，Cloudflare 日志仍受账号权限限制。上线前必须核对
实际 root/build/deploy/branch 设置，发布后再检查首页、导航页和 build-info.json。
