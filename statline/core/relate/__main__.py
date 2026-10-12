"""Usage: python -m statline.core.relate --adapter adapter.yaml --old old.csv --new new.csv"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from statline.core.adapters.compile import compile_adapter
from statline.core.adapters.load import load_spec
from statline.core.relate import load_pairwise_rows, score_pairwise
from statline.core.statpacks.package import dump_yaml, load_statpack_tree, unpack_statpack


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate an adapter-authored pairwise model")
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--old", type=Path)
    parser.add_argument("--new", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--select", action="append", help="Return this entity ID (repeat for A vs B)"
    )
    parser.add_argument(
        "--explain-pairs", action="store_true", help="Include opponent-by-opponent calculations"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="statline-pairwise-") as temp:
        yaml_path = args.adapter
        if yaml_path.suffix.casefold() == ".statpack":
            root = unpack_statpack(yaml_path, Path(temp) / "adapter")
            yaml_path = Path(temp) / "adapter.yaml"
            yaml_path.write_text(dump_yaml(load_statpack_tree(root)), encoding="utf-8")
        adapter = compile_adapter(load_spec(yaml_path))
        model = args.model or next(iter(adapter.pairwise))
        spec = adapter.pairwise[model]
        base = args.adapter.resolve().parent
        old_path = args.old or (base / spec.duo.get("old", ""))
        new_path = args.new or (base / spec.duo.get("new", ""))
        if not args.old and not spec.duo.get("old") or not args.new and not spec.duo.get("new"):
            parser.error("Specify --old and --new or provide pairwise.<model>.duo defaults")
        results = score_pairwise(
            adapter,
            old=load_pairwise_rows(old_path),
            new=load_pairwise_rows(new_path),
            model=model,
            selected_ids=args.select,
            include_pairs=args.explain_pairs,
        )
        serialized = json.dumps(results, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            args.out.write_text(serialized, encoding="utf-8")
        else:
            print(serialized, end="")


if __name__ == "__main__":
    main()
