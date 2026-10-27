"""Run the pytest suite and publish the result for the GitHub Pages site.

Usage: python scripts/build_test_report.py
Writes docs/assets/data/test-report.json (exit code mirrors pytest).
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets" / "data" / "test-report.json"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}"], cwd=ROOT)
        tree = ET.parse(junit)

    files: "OrderedDict[str, list]" = OrderedDict()
    for case in tree.iter("testcase"):
        module = case.get("classname", "").split(".")
        file = "/".join(module[:2]) + ".py" if len(module) >= 2 else case.get("file", "?")
        outcome = "passed"
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        files.setdefault(file, []).append({"name": case.get("name"), "outcome": outcome})

    tests = [t for ts in files.values() for t in ts]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": f"Python {platform.python_version()}",
        "total": len(tests),
        "passed": sum(t["outcome"] == "passed" for t in tests),
        "failed": sum(t["outcome"] == "failed" for t in tests),
        "skipped": sum(t["outcome"] == "skipped" for t in tests),
        "files": [
            {"file": f, "passed": sum(t["outcome"] == "passed" for t in ts), "tests": ts}
            for f, ts in files.items()
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['passed']}/{report['total']} passed -> {OUT.relative_to(ROOT)}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
