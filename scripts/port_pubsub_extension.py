#!/usr/bin/env python3
import os
import shutil

UPSTREAM_DIR = "/tmp/ka-upstream"
TARGET_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

def main():
    src_ext = os.path.join(UPSTREAM_DIR, "extensions", "pubsub-platform")
    dst_ext = os.path.join(TARGET_DIR, "extensions", "pubsub-platform")

    if os.path.exists(dst_ext):
        shutil.rmtree(dst_ext)

    print(f"Copying {src_ext} -> {dst_ext}")
    shutil.copytree(src_ext, dst_ext)
    print("Pubsub extension files successfully copied!")

    # Copy unit tests to verify
    for test_file in ["test_pubsub_adapter.py", "test_pubsub_e2e.py"]:
        src_test = os.path.join(UPSTREAM_DIR, "tests", test_file)
        dst_test = os.path.join(TARGET_DIR, "tests", test_file)
        if os.path.exists(src_test):
            print(f"Copying {src_test} -> {dst_test}")
            shutil.copy(src_test, dst_test)

if __name__ == "__main__":
    main()
