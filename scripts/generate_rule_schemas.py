"""
generate_rule_schemas.py — 从 Python 契约单源生成与校验 schemas/conflict-rules.schema.json
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from lib.rule_contract import export_json_schema


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify schemas/conflict-rules.schema.json from lib.rule_contract SSOT."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only drift check: compare schema against Python SSOT without mutating disk. Exits 1 on drift."
    )
    parser.add_argument(
        "--schema-path",
        type=Path,
        default=repo_root / "schemas" / "conflict-rules.schema.json",
        help="Target schema path"
    )
    args = parser.parse_args()

    schema_path: Path = args.schema_path
    schema_data = export_json_schema()
    expected_json = json.dumps(schema_data, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        if not schema_path.exists():
            print(f"❌ Error: Schema file not found: {schema_path}", file=sys.stderr)
            return 1

        actual_json = schema_path.read_text(encoding="utf-8")
        if actual_json == expected_json:
            print("✅ Rule schema is strictly up to date. (0 drift detected)")
            return 0

        diff = list(difflib.unified_diff(
            actual_json.splitlines(keepends=True),
            expected_json.splitlines(keepends=True),
            fromfile=str(schema_path),
            tofile="lib.rule_contract:export_json_schema()",
            n=3
        ))
        print(f"❌ Drift detected between {schema_path} and lib.rule_contract SSOT!\n", file=sys.stderr)
        sys.stderr.writelines(diff[:50])
        if len(diff) > 50:
            print(f"\n... ({len(diff) - 50} more diff lines omitted)", file=sys.stderr)
        print("\nRun 'python scripts/generate_rule_schemas.py' to update schema.", file=sys.stderr)
        return 1

    import os
    import tempfile

    schema_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=schema_path.parent,
        delete=False,
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    try:
        temp_file.write(expected_json)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        os.replace(str(temp_path), str(schema_path))
        if os.name == "posix":
            fd = os.open(str(schema_path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except Exception:
        try:
            temp_file.close()
        except Exception:
            pass
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise

    print(f"Generated {schema_path} successfully from lib.rule_contract!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
