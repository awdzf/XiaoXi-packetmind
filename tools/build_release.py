"""构建可发布的小析源码包（纯标准库，无第三方依赖）。

    python tools/build_release.py
    python tools/build_release.py --no-verify   # 只打包，不跑测试（不推荐）

产物：
    dist/xiaoxi-<version>/          干净源码副本
    dist/xiaoxi-<version>.zip       可直接上传到 GitHub Release
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

ROOT_FILES = [
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "VERSION",
    "CITATION.cff",
    "SECURITY.md",
    "PRIVACY.md",
    "CONTRIBUTING.md",
    "RELEASE.md",
    "xiaoxi.py",
    ".gitignore",
]
COPY_DIRS = ["experts", "expert-template", "tools"]
OPTIONAL_DIRS = [".github"]
#: 随发布包走的文档目录文件:只放"怎么用"的说明。
#: 用户自己放进 docs/ 的文档**绝不打包** —— 那是用户的资料,不是发布内容。
DOC_FILES = ["docs/README.md"]
TEXT_SUFFIXES = {".py", ".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".cff", ".jsonl"}
SKIP_DIRS = {"__pycache__", ".git", "dist", ".pytest_cache", ".venv", "venv"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
#: 本机绝对路径:只看"用户主目录"这一种形态,不看盘符。
#: 为什么不看盘符:`docs/README.md` 里就有 `D:\notes\x.md` 这种**示例**,扫它会假阳性。
LOCAL_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:[\\/]+Users[\\/]", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
]


def _version() -> str:
    """读并校验版本号:VERSION / framework.__version__ / CITATION.cff / CHANGELOG 四处。

    以前只认 `__version__ = "x"` 的双引号写法、且取第一处匹配:
    注释里的旧版本号会让它误报,单引号写法会让它说"未找到"。
    """
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise SystemExit(f"VERSION 不是合法版本号：{version!r}")
    init = (ROOT / "experts" / "framework" / "__init__.py").read_text(encoding="utf-8")
    found = set(re.findall(r'^__version__\s*=\s*["\']([^"\']+)["\']', init, re.MULTILINE))
    if found != {version}:
        raise SystemExit(
            f"VERSION ({version}) 与 framework.__version__ "
            f"({sorted(found) or '未找到'}) 不一致"
        )
    # CITATION.cff 里的版本号构建时不会自动改：对不上就停下来，别发出一个"自称旧版"的包
    citation = ROOT / "CITATION.cff"
    if citation.is_file():
        text = citation.read_text(encoding="utf-8")
        cited = re.search(r'^version:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
        if cited and cited.group(1).strip() != version:
            raise SystemExit(
                f"CITATION.cff 的 version ({cited.group(1).strip()}) 与 VERSION "
                f"({version}) 不一致：先同步，再构建"
            )
        released = re.search(r'^date-released:\s*"?([0-9-]+)"?\s*$', text, re.MULTILINE)
        if released and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", released.group(1)):
            raise SystemExit(
                f"CITATION.cff 的 date-released 不是 YYYY-MM-DD：{released.group(1)!r}"
            )
    # CHANGELOG 顶部那段必须是这一版:否则发布说明和产物对不上
    changelog = ROOT / "CHANGELOG.md"
    if changelog.is_file():
        headings = re.findall(r"^##\s+(.+)$", changelog.read_text(encoding="utf-8"),
                              re.MULTILINE)
        if headings and version not in headings[0]:
            raise SystemExit(
                f"CHANGELOG.md 的最新一段是「{headings[0]}」，不是 {version}:"
                "先把这一版的改动写进去，再构建"
            )
    return version


def _skip(path: Path, name: str) -> bool:
    if name in SKIP_DIRS or name.startswith(".tmp"):
        return True
    # 跳过隐藏的运行目录（保留仓库配置 .github）
    if name.startswith(".") and name != ".github":
        return True
    if path.suffix in SKIP_SUFFIXES:
        return True
    # 用户数据绝不进发布包
    if path.as_posix().endswith("experts/data"):
        return True
    return False


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=lambda _dir, names: {
            name for name in names if _skip(Path(_dir) / name, name)
        },
    )


def _stage(version: str) -> Path:
    stage = DIST / f"xiaoxi-{version}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for name in ROOT_FILES:
        source = ROOT / name
        if not source.is_file():
            raise SystemExit(f"缺少发布文件：{name}")
        shutil.copy2(source, stage / name)
    for name in COPY_DIRS:
        source = ROOT / name
        if not source.is_dir():
            raise SystemExit(f"缺少发布目录：{name}")
        _copy_tree(source, stage / name)
    for name in OPTIONAL_DIRS:
        source = ROOT / name
        if source.is_dir():
            _copy_tree(source, stage / name)
    for name in DOC_FILES:
        source = ROOT / name
        if not source.is_file():
            continue
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return stage


def _sanitize_memory(stage: Path) -> None:
    path = stage / "experts" / "packs" / "memory" / "pack.json"
    if not path.is_file():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["entries"] = []
    raw.pop("_next_user_id", None)
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sanitize_config(stage: Path) -> None:
    """把用户改过的开关恢复成出厂默认。

    `:trust` / `:priority` 会写回 `experts/config.json`(见 framework/settings.py);
    不清这一份,发布包里就带着上一个人的偏好(`max_packs: 7`、一串 trust 条目)。
    默认值只从 `settings.py` 取一处,不在这里另抄一份。
    """
    path = stage / "experts" / "config.json"
    if not path.is_file():
        return
    sys.path.insert(0, str(ROOT / "experts"))
    try:
        from framework import settings as settings_mod
    finally:
        sys.path.pop(0)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["max_packs"] = settings_mod.DEFAULT_MAX_PACKS
    raw["rehit_rounds"] = settings_mod.DEFAULT_REHIT_ROUNDS
    context = raw.setdefault("context", {})
    context["enabled"] = True
    context["max_turns"] = settings_mod.DEFAULT_MAX_TURNS
    context["max_keywords"] = settings_mod.DEFAULT_MAX_KEYWORDS
    trust = raw.setdefault("trust", {})
    trust["author_entries_high"] = True
    trust["high_confidence"] = []
    trust["low_confidence"] = []
    trust["conflict_priority"] = list(settings_mod.DEFAULT_CONFLICT_PRIORITY)
    trust["conflict_policy"] = settings_mod.DEFAULT_CONFLICT_POLICY
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".tmp") for part in rel.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        # 第二道防线:复制阶段已经跳过它,这里再挡一次 ——
        # 用户数据(`experts/data/missed.jsonl`)绝不能跟着 MANIFEST / zip 走。
        posix = rel.as_posix()
        if posix == "experts/data" or posix.startswith("experts/data/"):
            continue
        if posix == "MANIFEST.sha256":
            continue
        yield path, rel


def _text_of(path: Path) -> str | None:
    """把文件读成文本;二进制返回 None(UTF-16 也认,别让"换个编码"就绕过扫描)。"""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > 4_000_000:                      # 扫描只看前 4MB,别为一个大文件拖住构建
        data = data[:4_000_000]
    for encoding in ("utf-8", "utf-16"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _scan(stage: Path) -> None:
    """扫描**所有**文本文件,不再按后缀白名单放行。

    以前只扫 8 个后缀、只认 C 盘用户目录这一种写法:实测 `.jsonl`(本仓的用户数据格式)、
    `.gitignore`、UTF-16 文本都能把本机绝对路径和密钥带进发布包。
    """
    problems: list[str] = []
    for path, rel in _iter_files(stage):
        text = _text_of(path)
        if text is None:
            continue
        for pattern in LOCAL_PATH_PATTERNS:
            if pattern.search(text):
                problems.append(f"{rel}: 含本机绝对路径")
                break
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"{rel}: 命中疑似密钥模式 {pattern.pattern}")
                break
    if problems:
        raise SystemExit("发布包扫描失败：\n  " + "\n  ".join(problems))


def _run(command: list[str], cwd: Path, label: str) -> str:
    print(f"[verify] {label}")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"[verify] 失败：{label}")
    tail = [line for line in proc.stdout.splitlines() if line.strip()][-3:]
    for line in tail:
        print("  " + line)
    return proc.stdout


def _verify(stage: Path, version: str) -> None:
    python = sys.executable
    _run([python, "-m", "compileall", "-q", "experts", "expert-template", "xiaoxi.py"],
         stage, "compileall")
    _run([python, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
         stage / "experts", "experts tests")
    _run([python, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
         stage / "expert-template", "template tests")
    _run([python, "pack.py", "check"], stage / "experts", "pack health")
    _run([python, "eval.py"], stage / "experts", "retrieval eval")
    out = _run([python, "xiaoxi.py", "--version"], stage, "version")
    # 只查退出码抓不到"版本号写死在别处":这里要求它打出来的就是 VERSION。
    if version not in out:
        raise SystemExit(
            f"[verify] xiaoxi.py --version 没有输出 {version}（实际:{out.strip()!r}）"
        )


def _clean_caches(stage: Path) -> None:
    for directory in sorted(stage.rglob("__pycache__"), reverse=True):
        shutil.rmtree(directory, ignore_errors=True)
    for path in stage.rglob("*.py[co]"):
        path.unlink(missing_ok=True)


def _manifest(stage: Path) -> None:
    lines: list[str] = []
    for path, rel in _iter_files(stage):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {rel.as_posix()}")
    (stage / "MANIFEST.sha256").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _zip(stage: Path, version: str) -> Path:
    """先写 .part,全部写完再原子改名:半截 zip 不会冒充成品。"""
    target = DIST / f"xiaoxi-{version}.zip"
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    names: list[str] = []
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, rel in _iter_files(stage):
            name = f"xiaoxi-{version}/{rel.as_posix()}"
            archive.write(path, name)
            names.append(name)
        manifest = stage / "MANIFEST.sha256"
        if manifest.is_file():
            name = f"xiaoxi-{version}/MANIFEST.sha256"
            archive.write(manifest, name)
            names.append(name)
    # 自检:用户数据 / 字节码 / 缓存不许混进去
    bad = [n for n in names
           if "__pycache__" in n or n.endswith((".pyc", ".pyo"))
           or "/experts/data/" in n]
    if bad:
        partial.unlink(missing_ok=True)
        raise SystemExit("压缩包自检失败,混进了不该发布的东西：\n  " + "\n  ".join(bad[:10]))
    os.replace(partial, target)
    return target


def _force_utf8() -> None:
    """自己的输出也统一成 UTF-8:重定向到文件 / CI 日志里中文才不会乱码。

    `_run` 已经给子进程设了 PYTHONIOENCODING,但本进程没设:
    `python tools/build_release.py > build.log` 在中文 Windows 上会写成 GBK。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except Exception:                                            # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(description="构建小析发布包")
    parser.add_argument("--no-verify", action="store_true", help="跳过干净副本测试")
    args = parser.parse_args(argv)
    version = _version()
    DIST.mkdir(exist_ok=True)
    target = DIST / f"xiaoxi-{version}.zip"
    stale = target.with_name(target.name + ".stale")
    stage: Path | None = None
    try:
        stage = _stage(version)
        _sanitize_memory(stage)
        _sanitize_config(stage)
        _scan(stage)
        if not args.no_verify:
            _verify(stage, version)
        _clean_caches(stage)
        _manifest(stage)
        archive = _zip(stage, version)
    except BaseException:
        # 失败/被 Ctrl+C 打断时,不能把**上一次**的 zip 留在 dist 里冒充这一次的产物
        # (RELEASE.md 承诺"不会留下看起来成功的 zip")。改名为 .stale,字节还在,
        # 但一眼能看出它不是这一跑的结果。
        for leftover in (target.with_name(target.name + ".part"),):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass
        if target.exists():
            try:
                os.replace(target, stale)
                print(f"[构建失败] 旧的 {target.name} 已改名为 {stale.name}"
                      "(它不是这一次的产物,别上传)", file=sys.stderr)
            except OSError:
                pass
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        raise
    if stale.exists():
        stale.unlink(missing_ok=True)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    size = archive.stat().st_size
    print()
    print(f"发布包：{stage}")
    print(f"压缩包：{archive}（{size / 1024:.1f} KB）")
    print(f"SHA-256：{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
