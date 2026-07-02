# 发布 jiuwenswarm-instrumentor 到 PyPI

本包通过 **GitHub Actions + OIDC trusted publishing** 发布到 PyPI——**无需 API token、无需本地 twine upload**。工作流定义在 `.github/workflows/release.yml`。

> 旧的本地 `twine upload` + PyPI token 流程已废弃(被密钥检测拦截 + Windows GBK/粘贴问题搞坏过,别再走)。

## 前置(一次性,已配好)

1. **PyPI trusted publisher**:在 https://pypi.org/manage/project/jiuwenswarm-instrumentor/settings/publishing/ 添加一个 GitHub publisher:
   - owner: `Wendymayu`
   - repo: `jiuwenswarm-instrumentor`
   - workflow filename: `release.yml`
   - environment:(留空)
2. **默认分支必须是 `enterprise_dev`**(见下方「坑 1」)。确认:
   ```bash
   curl -s https://api.github.com/repos/Wendymayu/jiuwenswarm-instrumentor | grep default_branch
   # 应为 "default_branch": "enterprise_dev"
   ```
3. **工作流已注册**:
   ```bash
   curl -s https://api.github.com/repos/Wendymayu/jiuwenswarm-instrumentor/actions/workflows | grep -E 'name|state'
   # 应看到 name: release, state: active
   ```

## 常规发布(推荐)

### 1. 改版本号(两处)

版本号**单源**在 `_version.py`,`pyproject.toml` 通过 setuptools `dynamic` 自动读(`[tool.setuptools.dynamic] version = {attr = "jiuwenswarm_instrumentor._version.__version__"}`),不用改。每发一版只改:

| 文件 | 改什么 |
|---|---|
| `src/jiuwenswarm_instrumentor/_version.py` | `__version__ = "0.2.0"` → 新版本(真源) |
| `tests/test_smoke.py` | `assert __version__ == "0.2.0"` → 新版本(守卫,防止忘 bump) |

### 2. 本地预检(在打 tag 前先抓构建问题)

```bash
py -3.13 -m pip install --upgrade build twine
py -3.13 -m build
py -3.13 -m twine check dist/*
py -3.13 - <<'PY'
import zipfile, glob
f = glob.glob('dist/*.whl')[0]
names = zipfile.ZipFile(f).namelist()
assert 'jiuwenswarm_instrumentor.pth' in names, 'pth missing from wheel root'
assert any(n.endswith('_autoload.py') for n in names), '_autoload missing'
assert any(n.endswith('_env.py') for n in names), '_env missing'
print('wheel ok:', f)
PY
py -3.13 -m pytest -q   # 全套应通过
```

> `.pth` 必须在 wheel 根目录,靠 `setup.py` 自定义 `build_py` + `MANIFEST.in` 实现(data-files 对 wheel 不可靠)。工作流里有同样的校验,但本地先抓更快。

### 3. 提交 + 打 tag + 推送

在 `enterprise_dev` 分支上(发布 tag 都打在 `enterprise_dev`,不是默认的 PR 分支):

```bash
git commit -am "release: v0.3.0"
git tag v0.3.0
git push origin enterprise_dev
git push origin v0.3.0    # 推 tag 触发工作流
```

**推 tag 即触发** `.github/workflows/release.yml`:`checkout` → `python -m build` → `twine check` → 校验 `.pth` 在 wheel 根 → 通过 OIDC 发布到 PyPI。

### 4. 看运行

https://github.com/Wendymayu/jiuwenswarm-instrumentor/actions/workflows/release.yml

绿勾 = 发布成功(约 1–2 分钟)。

### 5. 验证上架

```bash
# per-version 端点(聚合 /json 会滞后 1-2 分钟,用这个更准)
curl -s https://pypi.org/pypi/jiuwenswarm-instrumentor/0.3.0/json | grep version
```

再装进干净 venv 验证 `.pth` 自动加载钩子真的发货了:

```bash
py -3.13 -m venv /tmp/verify
/tmp/verify/Scripts/python.exe -m pip install jiuwenswarm-instrumentor==0.3.0
/tmp/verify/Scripts/python.exe -c "import jiuwenswarm_instrumentor; print(jiuwenswarm_instrumentor.__version__)"
# 确认 .pth 在 site-packages
/tmp/verify/Scripts/python.exe -c "import site,os; p=site.getsitepackages()[1]; print([f for f in os.listdir(p) if f.endswith('.pth')])"
# 确认 .pth 在解释器启动时触发 _autoload(无需手动 import)
/tmp/verify/Scripts/python.exe -c "import sys; assert any('_autoload' in m for m in sys.modules); print('autoload OK')"
```

> Windows venv 的 `site.getsitepackages()[1]` 才是 `Lib\site-packages`;`[0]` 是 venv 根。`.pth` 应出现在 `[1]`。

## 追溯发布 / 手动触发(workflow_dispatch)

当 tag 在工作流存在**之前**就已推送(没触发),或想从某个分支手动发一版时:

1. 打开 https://github.com/Wendymayu/jiuwenswarm-instrumentor/actions/workflows/release.yml
2. 点 **"Run workflow"**
3. 选分支 **`enterprise_dev`**
4. 点绿色的 **"Run workflow"**

工作流从所选分支 HEAD 构建并发布。要求该分支 HEAD 的版本号已经是目标版本(三处已改)。

> 0.2.0 就是这样发的:`v0.2.0` tag 在 `release.yml` 提交之前就推了、没触发,改用 `workflow_dispatch` 从 `enterprise_dev` 发的。

## 踩过的坑

### 坑 1:工作流在 Actions 列表里不出现

**现象**:打开 Actions 页面看不到 `release` 工作流,"Run workflow" 按钮也没有,`workflow_dispatch` API 404。

**根因**:GitHub **只从默认分支注册工作流**。原来默认分支是 `feat/instrumentor-impl`,它没有 `release.yml`(工作流只在 `enterprise_dev` 上)→ GitHub 从没注册过这个工作流。

**解法**:把仓库默认分支改成 `enterprise_dev`(它有 `release.yml`):
- Settings → General → Default branch → 切到 `enterprise_dev` → Update

改完工作流立刻注册(state: active),"Run workflow" 可用,以后推 `v*` tag 也自动触发。

**验证默认分支**:
```bash
curl -s https://api.github.com/repos/Wendymayu/jiuwenswarm-instrumentor | grep default_branch
```

### 坑 2:`pypa/gh-action-pypi-publish@v1` 解析失败

**现象**:
```
Error: Unable to resolve action `pypa/gh-action-pypi-publish@v1`, unable to find version `v1`
```

**根因**:`pypa/gh-action-pypi-publish` 这个仓库**没有** `v1` 这个 tag 或 branch,只有具体版本 tag(`v1.14.0` 等)和浮动主版本**分支** `release/v1`。

**解法**:用 `@release/v1`(PyPA 官方推荐的浮动主版本引用,跟最新 v1.x):
```yaml
- name: Publish to PyPI (OIDC, no token)
  uses: pypa/gh-action-pypi-publish@release/v1
```
> 不要写 `@v1`,也不要随便 pin 到 `@v1.14.0`(除非要锁版本,但锁了之后新版 bugfix 拿不到)。

## 为什么是这套设计

- **OIDC trusted publishing**:GitHub Actions 用一次性 OIDC token 向 PyPI 认证,本地不存任何 API token,绕开了密钥检测 + Windows 粘贴破坏 token 的问题。
- **`.pth` 在 wheel 根**:装包即自动加载,`jiuwenclaw.app` fork 出的 agentserver/gateway 子进程各自跑 site 初始化 → 各自自动激活,无需 CLI 包裹父进程(`jiuwen-instrument jiuwenclaw.app` 只覆盖父进程,子进程漏掉——别用)。
- **editable 不发货 `.pth`**:`pip install -e .` 不会装 `.pth`,要自动加载必须非 editable 安装。
