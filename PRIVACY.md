# 隐私说明

## 一句话

小析默认**完全离线**：没有账号、没有遥测、没有网络请求；你教的知识只写在本机。

## 会写哪些文件

| 路径 | 内容 | 什么时候写 |
|---|---|---|
| `experts/packs/memory/pack.json` | 你确认写入的记忆条目（题面、答案、关键词、原话） | 每次 `#问题~答案` 并确认 `y` |
| `experts/data/missed.jsonl` | 没接住的问题与关键词（约 256KB 上限） | 一轮没有任何回答时 |
| `experts/packs/*/pack.json` | 作者/你手改的数据包 | 由你手动编辑 |

没有云同步、没有崩溃上报、没有使用统计。

## 会读哪些文件

启动时读取 `experts/packs/*/pack.json`、`experts/config.json`；导入笔记时读取你指定的
`.md` / `.txt` / `.csv`。不会扫描项目目录之外的文件。

## 删除

- 忘掉一条记忆：对话里 `#-<关键词或原话>`，先确认再删；
- 清空全部记忆：把 `experts/packs/memory/pack.json` 的 `entries` 改成 `[]`；
- 清空漏问：`:missed clear`；
- 彻底删除：删掉整个项目目录即可。

## 发布包

`tools/build_release.py` 生成的发布包会**清空记忆包**、删除 `experts/data/`，
不会把你的个人记忆带进 zip。
