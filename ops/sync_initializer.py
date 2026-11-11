"""Keep the self-contained installer synchronized with checked-in source, never .env."""
import argparse
import ast
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"^cat << 'EOF' > ([^\n]+)\n(.*?)^EOF\n", re.M | re.S)
IGNORED = {"node_modules", ".next", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "coverage"}
GENERATED_TYPES = '/// <reference types="next" />\n/// <reference types="next/image-types/global" />\n'


def sources():
    files = {Path(name) for name in (".gitignore", ".env.example", "docker-compose.yml", "README.synapse.md")}
    for tree in ("backend", "frontend", "ops", ".github"):
        for directory, dirs, names in os.walk(ROOT / tree, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED)
            for name in sorted(names):
                if name in {"package-lock.json", "tsconfig.tsbuildinfo"} or name.endswith((".pyc", ".pyo")):
                    continue
                if name.startswith(".env") and name != ".env.example":
                    continue
                path = Path(directory) / name
                if path.is_symlink():
                    raise SystemExit(f"Refusing to embed a symlink: {path.relative_to(ROOT)}")
                files.add(path.relative_to(ROOT))
    return files


def render(script):
    matches = list(PATTERN.finditer(script))
    tail = re.search(r"^# Generate local-only secrets.*$", script, re.M)
    if not matches or tail is None:
        raise SystemExit("Unsupported initializer format")
    prefix = script[:matches[0].start()]
    # Retain the original initializer in generated projects so synchronization also works there.
    declaration = 'SYNAPSE_SOURCE="$(python3 -c \'import os,sys; print(os.path.realpath(sys.argv[1]))\' "${BASH_SOURCE[0]:-}")"\n'
    if declaration not in prefix:
        prefix = prefix.replace('TARGET="${1:-synapse}"', declaration + 'TARGET="${1:-synapse}"')
    suffix = script[tail.start():]
    suffix = suffix.replace("chmod +x ops/deploy.sh\n", "chmod +x ops/deploy.sh ops/verify.sh\n")
    copy_source = '''if [[ -f "${SYNAPSE_SOURCE:-}" && ! -e init_synapse.sh ]]; then
  cp -- "$SYNAPSE_SOURCE" init_synapse.sh
  chmod +x init_synapse.sh
fi
'''
    if copy_source not in suffix:
        suffix = suffix.replace("chmod +x ops/deploy.sh ops/verify.sh\n", "chmod +x ops/deploy.sh ops/verify.sh\n" + copy_source)
    available = sources()
    ordered = [Path(match.group(1)) for match in matches]
    if len(ordered) != len(set(ordered)):
        raise SystemExit("Initializer contains duplicate file targets")
    missing = set(ordered) - available
    if missing:
        raise SystemExit("Missing source: " + ", ".join(map(str, sorted(missing))))
    ordered += sorted(available - set(ordered))
    parts = [prefix]
    for relative in ordered:
        content = GENERATED_TYPES if str(relative) == "frontend/next-env.d.ts" else (ROOT / relative).read_text()
        if "EOF" in content.splitlines():
            raise SystemExit(f"Heredoc delimiter collision: {relative}")
        if content and not content.endswith("\n"):
            content += "\n"
        if relative.suffix == ".py":
            ast.parse(content, filename=str(relative))
        parts.append(f"cat << 'EOF' > {relative.as_posix()}\n{content}EOF\n\n")
    return "".join(parts) + suffix, len(ordered)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    target = ROOT / "init_synapse.sh"
    if not target.is_file() or target.is_symlink():
        raise SystemExit("Run this from a project containing the original init_synapse.sh")
    original = target.read_text()
    updated, count = render(original)
    if args.check:
        if updated != original:
            raise SystemExit("Initializer drift detected. Run: python3 ops/sync_initializer.py --write")
        print(f"Initializer verified: {count} source files; generated Next.js types normalized")
        return
    fd, temporary = tempfile.mkstemp(prefix=".synapse-init-", dir=ROOT)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(updated)
        subprocess.run(["bash", "-n", temporary], check=True)
        os.chmod(temporary, 0o755)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f"Initializer synchronized: {count} embedded source files")


if __name__ == "__main__":
    main()
