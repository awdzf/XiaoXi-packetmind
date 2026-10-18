# 发布说明（1.0.0）

小析这一版按**源码发行包**发布，不发布到 PyPI：它靠目录结构发现 `experts/packs/`，PyPI 的 wheel 安装方式反而会掩盖“数据包随手可拆装”这个核心设计。

## 发布物

```text
dist/
├── xiaoxi-1.0.0/
│   ├── README.md
│   ├── LICENSE
│   ├── CHANGELOG.md
│   ├── VERSION
│   ├── CITATION.cff
│   ├── SECURITY.md
│   ├── PRIVACY.md
│   ├── CONTRIBUTING.md
│   ├── RELEASE.md
│   ├── xiaoxi.py
│   ├── tools/build_release.py
│   ├── experts/
│   └── expert-template/
└── xiaoxi-1.0.0.zip
```

`tools/build_release.py` 会在 `dist/` 下生成一个**干净副本**并打包。发布包：

- 不含 `.dsh/`、`dist/`、`__pycache__/`、`*.pyc`；
- 不含 `experts/data/`（漏问日志）；
- 记忆包 `entries` 会被清空，不带走任何用户教的知识；
- 生成 `MANIFEST.sha256`，并打印 zip 的 SHA-256。

## 构建

```bash
python tools/build_release.py
```

它会依次做：

1. 复制源码到 `dist/xiaoxi-<version>/`；
2. 清空记忆包、删掉日志/缓存；
3. 扫描明显的敏感串（本机绝对路径、常见 key 前缀）；
4. 在**副本里**跑：
   - `python -m compileall`
   - `python -m unittest discover -s experts/tests -t experts`
   - `python -m unittest discover -s expert-template/tests -t expert-template`
   - `python experts/pack.py check`
   - `python experts/eval.py`
5. 生成 `MANIFEST.sha256` 与 zip。

如果任何一步失败，构建会以非零退出码结束，不会留下“看起来成功”的 zip。

## 上传

### GitHub Release

1. 建仓库，推源码（不要推 `dist/`、`.dsh/`、`experts/data/`）；
2. 打 tag：`v1.0.0`；
3. 在 Release 里上传 `dist/xiaoxi-1.0.0.zip`；
4. Release notes 直接摘 `CHANGELOG.md` 的 1.0.0 段；
5. 仓库描述建议写清楚：**离线、不调用模型、零依赖、答案逐条可查**。

### 博客 / 文章

建议把下面三件事写在最前面，避免读者误以为它是“小号大模型”：

- 它是**检索式**的：知识来自你写的数据包，答案不生成、只引用；
- 它**不联网、不调用模型**，所以不知道实时信息，也不会“推理”开放问题；
- 它的长处是**可审计**：每条答案能追到数据包和条目 id，不知道就直说。

## 发布前人工检查

- [ ] `python -m unittest discover -s experts/tests -t experts` 全绿（当前 222 项）；
- [ ] `python experts/pack.py check` 全绿（当前 8 个包）；
- [ ] `python experts/eval.py` 指标没有明显回退（当前 21/21、21/21、MRR 1.000、负例 5/5）；
- [ ] `experts/packs/memory/pack.json` 的 `entries` 为空；
- [ ] 没有 `experts/data/`、没有 `.dsh/`、没有 API key/个人路径；
- [ ] `LICENSE` 与 `CITATION.cff` 的作者/年份符合你的意愿；
- [ ] 如果换了许可证，同步改 `CITATION.cff` 的 `license`。

## 版本升级

1. 改 `VERSION`；
2. 改 `experts/framework/__init__.py` 的 `__version__`；
3. 在 `CHANGELOG.md` 顶部加一段；
4. 跑 `python tools/build_release.py`；
5. 打 tag 并上传新的 zip。

## 许可证

本发布包默认使用 **MIT**。如果你希望换成 Apache-2.0、CC BY 4.0 或其它许可证，直接改 `LICENSE`，并同步改 `CITATION.cff` 的 `license` 字段即可。
