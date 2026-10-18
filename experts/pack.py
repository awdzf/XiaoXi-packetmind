"""数据包管理:给小析加/减一块知识 = 加/删一个目录。

    python experts/pack.py list                      # 现在有哪些数据包
    python experts/pack.py new my_pack --title 我的领域   # 从模板建一个数据包
    python experts/pack.py import notes.md --name my_notes --title 我的笔记
                                                     # 把 .md/.txt 笔记导入成一个数据包
    python experts/pack.py import qa.csv --name my_faq --title 我的问答
                                                     # 每行一条 Q&A 的 CSV 也可以
    python experts/pack.py import notes.md --dry-run # 只看会切成几块,不写盘
    python experts/pack.py check                     # 给所有数据包跑 --health
    python experts/pack.py check math emotion        # 只查几个

为什么需要它:小析是**一个人格**(`experts/core/smallai/persona.py`),
知识是**数据包**;核心代码只有一份(`experts/core/smallai/`),数据包只有数据 ——
`experts/packs/<名字>/` 里是 `pack.json`,可选 `kb.json` / `word_vectors.json`,
没有一行 Python。所以:

    加一块知识   =  `pack.py new <名字>`,然后改它的 pack.json
    导入一份笔记 =  `pack.py import notes.md`(零依赖;智能分块见 framework/ingest.py)
    减一块知识   =  删掉 `experts/packs/<名字>/` 这个目录(文件管理器里删)
    改一块知识   =  只改它自己的 pack.json,核心与人格都不用动

`check` 会真的起子进程跑核心的 `--health`(结构自检 + 规则正则 + 原题覆盖 + 端到端链路),
不是"看一眼文件在不在"。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent              # experts/
sys.path.insert(0, str(ROOT))

from framework import ingest  # noqa: E402
from framework.catalog import CORE_RUN, PACKS_DIR, discover  # noqa: E402
from framework.relations import PackGraph  # noqa: E402

TEMPLATE_PACK = ROOT.parent / "expert-template" / "pack.json"
TEMPLATE_README = ROOT.parent / "expert-template" / "README.md"
BUNDLED_PACK = ROOT / "core" / "smallai" / "domain" / "pack.json"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------
def cmd_list() -> int:
    cards = discover()
    graph = PackGraph.from_cards(cards)
    print("=" * 72)
    print(f"数据包 {len(cards)} 个(核心代码 1 份:{CORE_RUN.parent})")
    print("=" * 72)
    if not cards:
        print(f"  一个也没有。放一个 {PACKS_DIR}\\<名字>\\pack.json 就会出现。")
    for card in cards:
        role = " · 记忆包" if card.is_memory else (" · 兜底包" if card.is_general else "")
        relation = graph.describe(card.folder)
        print(f"\n数据包 {card.folder}({card.name}){role}")
        print(f"  目录:{card.pack_dir}")
        if relation:
            print(f"  关系:{relation}")
        print(f"  {card.tagline}")
        print(f"  条目 {len(card.entries)} 条")
        if card.has_structure:
            print(
                f"  结构化: 事实 {len(card.facts)} / 规则 {len(card.rules)} / "
                f"关系 {len(card.relations)}"
            )
    print()
    return 0


# --------------------------------------------------------------------------
# new
# --------------------------------------------------------------------------
def cmd_new(name: str, title: str | None) -> int:
    if not _SLUG_RE.match(name):
        print(f"[拒绝] 名字要用小写字母/数字/下划线/连字符:{name!r}")
        return 2
    target = PACKS_DIR / name
    if target.exists():
        print(f"[拒绝] 已经存在:{target}(改它的 pack.json,或换一个名字)")
        return 2

    source = TEMPLATE_PACK if TEMPLATE_PACK.is_file() else BUNDLED_PACK
    raw = json.loads(source.read_text(encoding="utf-8"))
    expert = raw.setdefault("expert", {})
    expert["id"] = name
    expert["name"] = title or name
    expert["tagline"] = "（新建数据包）现在的条目是模板示例:把 scope / entries 换成你的领域"

    target.mkdir(parents=True)
    (target / "pack.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if TEMPLATE_README.is_file():
        (target / "README.md").write_text(
            TEMPLATE_README.read_text(encoding="utf-8"), encoding="utf-8"
        )

    print(f"已建数据包:{target}")
    print(f"  来源模板:{source}")
    print("  接下来:")
    print(f"    1) 改 {target / 'pack.json'} 的 expert.scope 与 entries(现在还是模板示例)")
    print(f"    2) 体检:python experts/pack.py check {name}")
    print(f'    3) 试问:python experts/core/run.py --pack "{target}" "你的标准问题"')
    print()
    return cmd_check([name])


# --------------------------------------------------------------------------
# import:把 .md/.txt 笔记变成一个数据包(零依赖、智能分块)
# --------------------------------------------------------------------------
def _slugify(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    if not value:
        # 纯中文文件名:给一个稳定后缀,避免不同文件都叫 notes
        value = "notes_" + hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:6]
    return value[:40]


def _template_safety() -> dict:
    """从模板/自带包抄一份公共安全规则;抄不到就留空(框架底线仍然生效)。"""
    for path in (TEMPLATE_PACK, BUNDLED_PACK):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        safety = raw.get("safety") if isinstance(raw, dict) else None
        if isinstance(safety, dict):
            return safety
    return {}


def cmd_import(
    source_arg: str,
    name: str | None,
    title: str | None,
    *,
    force: bool = False,
    dry_run: bool = False,
    max_tokens: int = ingest.MAX_TOKENS_WHOLE,
    chunk_tokens: int = ingest.CHUNK_TOKENS,
    overlap: int = ingest.OVERLAP_TOKENS,
) -> int:
    """把一份 Markdown / 纯文本导入成数据包;先量令牌,没超预算就整篇一块。"""
    source = Path(source_arg).expanduser()
    if not source.is_file():
        print(f"[拒绝] 找不到文件:{source}")
        return 2

    name = name or _slugify(source.stem)
    if not _SLUG_RE.match(name):
        print(f"[拒绝] 数据包名字要用小写字母/数字/下划线/连字符:{name!r}")
        return 2
    target = PACKS_DIR / name
    if target.exists() and not force:
        print(f"[拒绝] 数据包已存在:{target}")
        print("       要重建就加 --force(会覆盖这个目录里的 pack.json 与 README.md)")
        return 2

    max_tokens = max(200, int(max_tokens))
    chunk_tokens = max(100, min(int(chunk_tokens), max_tokens))
    overlap = max(0, min(int(overlap), chunk_tokens // 2))

    try:
        if source.suffix.lower() == ".csv":
            rows = ingest.read_csv_pairs(source)
            result = ingest.build_csv_pack(
                rows,
                source_name=source.name,
                name=name,
                title=title or source.stem,
                safety=_template_safety(),
                source_bytes=source.stat().st_size,
            )
        else:
            text = ingest.read_document(source)
            result = ingest.build_pack(
                text,
                source_name=source.name,
                name=name,
                title=title or source.stem,
                safety=_template_safety(),
                max_tokens_whole=max_tokens,
                chunk_tokens=chunk_tokens,
                overlap=overlap,
            )
    except (OSError, ValueError) as exc:
        print(f"[拒绝] 导入失败:{exc}")
        return 2

    pack_title = result.pack["expert"]["name"]
    print("=" * 72)
    print(f"导入 {source.name} -> 数据包 {name}({pack_title})")
    print(f"  文件大小 : {result.bytes / 1024:.1f} KB")
    if result.kind == "csv":
        print(f"  估算令牌 : {result.tokens}(问题 + 答案的保守估算)")
    else:
        print(f"  估算令牌 : {result.tokens}(阈值 {max_tokens};单块上限 {chunk_tokens};"
              f"重叠 {overlap})")
    if result.kind == "csv":
        print(f"  结果     : {result.entries} 条 Q&A(CSV 每行一条)")
        print("  分块策略 : CSV 不切段:一行就是一条问答")
    else:
        print(f"  结果     : {result.chunks} 块 / {result.entries} 条")
        if result.tokens <= max_tokens:
            if result.chunks == 1:
                print("  分块策略 : 没超过阈值 -> 整篇一块(不为了切而切)")
            else:
                print("  分块策略 : 没超过阈值 -> 按标题分条(每条正文不切)")
        else:
            print("  分块策略 : 超过阈值 -> 按标题/段落/句子切,块间带重叠")
    print("=" * 72)
    for entry in result.pack["entries"][:5]:
        print(f"  · {entry['question']}")
        print(f"    关键词:{'、'.join(entry['keywords'][:8])}")
    if result.entries > 5:
        print(f"  …其余 {result.entries - 5} 条见 pack.json")
    print("  说明:answer 就是原文;令牌数是本地保守估算,不是模型真实分词器。")

    if dry_run:
        print("\n(预演:没有写盘。去掉 --dry-run 才会生成数据包)\n")
        return 0

    ingest.write_pack(target, result.pack)
    if result.kind == "csv":
        detail = (
            f"- 来源格式:CSV(每行一条 Q&A),共 {result.entries} 条 / 约 {result.tokens} 估算令牌\n"
            "- 每条 answer 就是 response 列原文;question/aliases/keywords 是检索键\n"
        )
    else:
        detail = (
            f"- 分块:{result.chunks} 块 / {result.entries} 条 / 约 {result.tokens} 估算令牌\n"
            f"- 分块阈值:整篇 ≤ {max_tokens} 令牌不分块;超过后每块 ≤ {chunk_tokens},"
            f"重叠 {overlap}\n"
            "- 每条 answer 都是原文;question/aliases/keywords 是从标题与正文抽出的检索键\n"
        )
    (target / "README.md").write_text(
        f"# {pack_title}\n\n"
        f"从 `{source.name}` 导入的数据包(由 `experts/pack.py import` 生成)。\n\n"
        f"- 源文件:{source.name}(sha1 `{result.sha1[:12]}`)\n"
        + detail
        + "- 没有模型参与:分块与关键词都是本地规则,耗时与内存都很小\n\n"
        "重新导入(同一份文件会得到同一批 id):\n\n"
        f"```bash\npython experts/pack.py import \"{source}\" --name {name} --force\n```\n",
        encoding="utf-8",
    )

    print(f"\n已写入:{target / 'pack.json'}")
    print(f"  体检:python experts/pack.py check {name}")
    print(f'  试问:python experts/GoChat.py "{result.pack["entries"][0]["question"]}"')
    print()
    return cmd_check([name])


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------
def cmd_check(names: list[str]) -> int:
    # 每次重新发现:刚用 `new` 建出来的包还没进模块级快照
    cards = [card for card in discover() if not names or card.folder in names]
    missing = sorted(set(names) - {card.folder for card in cards})
    for name in missing:
        print(f"[缺失] {name}:没有这个数据包(看 python experts/pack.py list)")

    failed = 0
    print("=" * 72)
    print(f"体检 {len(cards)} 个数据包(核心:{CORE_RUN})")
    print("=" * 72)
    for card in cards:
        proc = subprocess.run(
            [sys.executable, str(CORE_RUN), "--pack", str(card.pack_dir), "--health"],
            cwd=str(card.pack_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        ok = proc.returncode == 0
        failed += 0 if ok else 1
        mark = "OK  " if ok else "失败"
        print(f"  [{mark}] {card.folder:<9} {card.name:<9} 条目 {len(card.entries):>3} 条")
        if not ok:
            for line in (proc.stdout + proc.stderr).strip().splitlines():
                print("        " + line)
    print()
    # 关系要拿**全部**包来校验(只查一个包时,它引用的父包不该被当成"不存在")
    relation_problems = PackGraph.from_cards(discover()).problems()
    if relation_problems:
        failed += len(relation_problems)
        print("数据包关系有问题(包含/被包含):")
        for problem in relation_problems:
            print("  · " + problem)
        print()
    if failed:
        print(f"{failed} 处没通过 —— 按上面的问题清单改 pack.json,别绕过体检。")
    else:
        print("全部通过。加/删知识只动 experts/packs/ 下的目录,核心与人格都不用改。")
    return 0 if failed == 0 and not missing else 1


# --------------------------------------------------------------------------
def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pack",
        description="数据包管理:列 / 新建 / 导入笔记 / 体检(小析是人格,知识是数据包)",
    )
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "new", "check", "import"], help="默认 list")
    p.add_argument("names", nargs="*",
                   help="new:新名字;check:要查的数据包(省略=全部);import:源文件")
    p.add_argument("--title", default=None, help="new/import:显示名(省略=用名字/文件名)")
    p.add_argument("--name", default=None, help="import:数据包名(省略=从文件名推)")
    p.add_argument("--force", action="store_true", help="import:覆盖已存在的数据包")
    p.add_argument("--dry-run", action="store_true", help="import:只预演,不写盘")
    p.add_argument("--max-tokens", type=int, default=ingest.MAX_TOKENS_WHOLE,
                   help=f"import:整篇不超过这个估算令牌数就不分块(默认 {ingest.MAX_TOKENS_WHOLE})")
    p.add_argument("--chunk-tokens", type=int, default=ingest.CHUNK_TOKENS,
                   help=f"import:超过阈值后单块上限(默认 {ingest.CHUNK_TOKENS})")
    p.add_argument("--overlap", type=int, default=ingest.OVERLAP_TOKENS,
                   help=f"import:相邻块重叠的估算令牌数(默认 {ingest.OVERLAP_TOKENS})")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except Exception:                                            # noqa: BLE001
            pass
    args = build_argparser().parse_args(argv)
    if args.action == "new":
        if not args.names:
            print("用法:python experts/pack.py new <名字> [--title 显示名]")
            return 2
        return cmd_new(args.names[0], args.title)
    if args.action == "import":
        if not args.names:
            print("用法:python experts/pack.py import <notes.md|notes.txt> "
                  "[--name 包名] [--title 显示名] [--dry-run] [--force]")
            return 2
        return cmd_import(
            args.names[0],
            args.name,
            args.title,
            force=args.force,
            dry_run=args.dry_run,
            max_tokens=args.max_tokens,
            chunk_tokens=args.chunk_tokens,
            overlap=args.overlap,
        )
    if args.action == "check":
        return cmd_check(args.names)
    return cmd_list()


if __name__ == "__main__":
    raise SystemExit(main())
