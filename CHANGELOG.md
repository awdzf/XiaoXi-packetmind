# 更新日志

## 1.0.0 — 2026-10-18

第一个可发布版本。目标不是“更大的模型”，而是**可审计、可拆装、零依赖的本地知识应答器**。

### 核心

- 一个人格（`experts/core/smallai/persona.py`）+ 若干个可拆装数据包（`experts/packs/*/pack.json`）。
- 纯 Python 标准库，无网络、无模型、无第三方依赖；答案只引用条目原句，逐条给出来源。
- 单进程内核 + 按包流水线缓存；`--subprocess` 保留隔离路径。
- 安全闸先于分解；危机场景区分“自己 / 身边人”；拒答不把内部规则名递给用户。

### 检索

- 规则检索优先；BM25 倒排 + TF-IDF + 分层索引做混合兜底。
- 查询扩展：首轮覆盖不足时，从高分文档取词再检索一次。
- 校准弃权：z 分数、margin、coverage、trust 合成置信度，低于阈值不硬答。
- 倒排索引按包增量更新；教完一条记忆只更新记忆包。
- World Info 式预算：条目 `priority` / `group`，`max_entries` / `text_budget`。

### 学习与推理

- `#问题~答案` 教一道题；旧写法 `#一句话` 兼容。
- 同题再教自动更新答案；`#!问题~新答案` 显式修正，旧答案留在 `revisions`（CBR revise）。
- `#?` 原话回放，`#-` 先列后删。
- 记忆包与作者包冲突时默认 `author > memory`，低优先级仍列出并标注。
- 可选 `facts` / `rules` / `relations`，以及 Datalog/Prolog 式子集（递归、证明树、有界否定）。

### 评测

- `experts/evaluation/cases.jsonl`：26 条检索用例（21 正例 / 5 负例）。
- `python experts/eval.py` / `python experts/GoChat.py --eval`：
  文件夹 hit@1、条目 hit@1、MRR、负例弃权率。
- 222 项自动化测试；8 个数据包体检通过。

### 发布

- MIT 许可证；`CITATION.cff`；`VERSION`。
- `tools/build_release.py` 生成 `dist/xiaoxi-1.0.0/` 与 `.zip`，构建后会在干净副本上跑全量测试。
- 发布包不包含用户记忆、漏问日志、`__pycache__`、`.dsh`。
