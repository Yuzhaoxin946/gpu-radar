#!/usr/bin/env python3
"""Build a dependency-free Python 3.6+ Agent executable without pip."""

import argparse
from pathlib import Path
import shutil
import tempfile
import zipapp


def build(output):
    source = Path(__file__).resolve().parent / "src" / "gpu_radar_agent"
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gpu-radar-zipapp-") as directory:
        root = Path(directory)
        package = root / "gpu_radar_agent"
        package.mkdir()
        for path in sorted(source.glob("*.py")):
            shutil.copyfile(str(path), str(package / path.name))
        (root / "__main__.py").write_text(
            "from gpu_radar_agent.cli import main\nraise SystemExit(main())\n",
            encoding="utf-8",
        )
        # No compressed= argument: it is unavailable in Python 3.6.
        zipapp.create_archive(str(root), target=str(output), interpreter="/usr/bin/env python3")
    print("Built {} (Python 3.6+, no pip dependencies)".format(output))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "gpu-radar-agent.pyz"))
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
