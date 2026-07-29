#!/usr/bin/env python3
import subprocess
import os

def main():
    out = subprocess.check_output(["git", "diff", "--name-status", "HEAD..upstream-feature-modular-agents"], text=True)
    lines = out.strip().splitlines()
    print(f"Total differing files between HEAD and upstream: {len(lines)}")
    
    categories = {}
    for line in lines:
        status, path = line.split(maxsplit=1)
        top_dir = path.split("/")[0] if "/" in path else "."
        categories.setdefault(top_dir, []).append((status, path))

    for cat, items in categories.items():
        print(f"\n--- Category: {cat} ({len(items)} files) ---")
        for status, path in items[:15]:
            print(f"  {status}\t{path}")
        if len(items) > 15:
            print(f"  ... and {len(items)-15} more")

if __name__ == "__main__":
    main()
