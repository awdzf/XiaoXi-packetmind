# 参与贡献

## 最重要的三条

1. **数据包里不放 `.py`**：一块知识 = 一个 `experts/packs/<名字>/` 目录 + `pack.json`；
2. **不引入运行时依赖**：目标环境是“小设备 + 无网络”，只允许 Python 3.10+ 标准库；
3. **答案必须可追溯**：新增能力不能绕过“引用条目原句 + 数据包/条目 id”的纪律。

## 加一块知识

```bash
python experts/pack.py new my_pack --title 我的领域
# 改 experts/packs/my_pack/pack.json
python experts/pack.py check my_pack
python experts/GoChat.py "你的标准问题"
```

如果新包需要回归探针，在 `experts/tests/test_packs.py` 的 `PROBES` 里补三条：
canonical / miss / offtopic。

## 改检索/推理

- 先跑 `python experts/eval.py` 记下基线；
- 改完再跑一次，指标不能明显回退；
- 跑 `python -m unittest discover -s experts/tests -t experts`（当前 222 项）；
- 新增行为要有独立测试，不要只改现有断言。

## 提交信息

建议格式：

```text
feat: 加 Datalog 递归规则
fix: 修正记忆包 Q&A 的 raw 回放
docs: 补发布说明
test: 覆盖预算裁剪
```

## 许可证

贡献即表示同意以本仓库的 MIT 许可证发布。
