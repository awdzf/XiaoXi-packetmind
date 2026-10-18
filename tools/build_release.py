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
TEXT_SUFFIXES = {".py", ".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".cff"}
SKIP_DIRS = {"__pycache__", ".git", "dist", ".pytest_cache", ".venv", "venv"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
# 拼接写，避免扫描器自己的源码里出现完整的本机路径字面量
LOCAL_PATH_MARKERS = ("C:" + "\\Users\\", "C:" + "/Users/")


def _version() -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise SystemExit(f"VERSION 不是合法版本号：{version!r}")
    init = (ROOT / "experts" / "framework" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if not match or match.group(1) != version:
        raise SystemExit(
            f"VERSION ({version}) 与 framework.__version__ "
            f"({match.group(1) if match else '未找到'}) 不一致"
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


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".tmp") for part in rel.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        if rel.as_posix() == "MANIFEST.sha256":
            continue
        yield path, rel


def _scan(stage: Path) -> None:
    problems: list[str] = []
    for path, rel in _iter_files(stage):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(marker in text for marker in LOCAL_PATH_MARKERS):
            problems.append(f"{rel}: 含本机绝对路径")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"{rel}: 命中疑似密钥模式 {pattern.pattern}")
                break
    if problems:
        raise SystemExit("发布包扫描失败：\n  " + "\n  ".join(problems))


def _run(command: list[str], cwd: Path, label: str) -> None:
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


def _verify(stage: Path) -> None:
    python = sys.executable
    _run([python, "-m", "compileall", "-q", "experts", "expert-template", "xiaoxi.py"],
         stage, "compileall")
    _run([python, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
         stage / "experts", "experts tests")
    _run([python, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
         stage / "expert-template", "template tests")
    _run([python, "pack.py", "check"], stage / "experts", "pack health")
    _run([python, "eval.py"], stage / "experts", "retrieval eval")
    _run([python, "xiaoxi.py", "--version"], stage, "version")


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
    target = DIST / f"xiaoxi-{version}.zip"
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, rel in _iter_files(stage):
            archive.write(path, f"xiaoxi-{version}/{rel.as_posix()}")
        manifest = stage / "MANIFEST.sha256"
        if manifest.is_file():
            archive.write(manifest, f"xiaoxi-{version}/MANIFEST.sha256")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建小析发布包")
    parser.add_argument("--no-verify", action="store_true", help="跳过干净副本测试")
    args = parser.parse_args(argv)
    version = _version()
    DIST.mkdir(exist_ok=True)
    stage = _stage(version)
    _sanitize_memory(stage)
    _scan(stage)
    if not args.no_verify:
        _verify(stage)
    _clean_caches(stage)
    _manifest(stage)
    archive = _zip(stage, version)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    size = archive.stat().st_size
    print()
    print(f"发布包：{stage}")
    print(f"压缩包：{archive}（{size / 1024:.1f} KB）")
    print(f"SHA-256：{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
