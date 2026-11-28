"""Write the Homebrew formula for a release from its SHA256SUMS file.

    python scripts/homebrew_formula.py 0.8.3 dist/SHA256SUMS > Formula/lokra.rb

The release workflow commits the output to github.com/lokrahq/homebrew-tap, which is what
`brew install lokrahq/tap/lokra` reads.
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = "https://github.com/lokrahq/lokra/releases/download"
BLOCKS = (("macos", "arm64", "macos", "arm"), ("macos", "x86_64", "macos", "intel"),
          ("linux", "arm64", "linux", "arm"), ("linux", "x86_64", "linux", "intel"))


def formula(version: str, sums: dict[str, str], base: str = BASE) -> str:
    parts = []
    for system in ("macos", "linux"):
        inner = []
        for build_os, arch, brew_os, brew_arch in BLOCKS:
            if build_os != system:
                continue
            name = f"lokra-{version}-{build_os}-{arch}.tar.gz"
            inner.append(f'    on_{brew_arch} do\n      url "{base}/v{version}/{name}"\n      sha256 "{sums[name]}"\n    end')
        parts.append(f"  on_{system} do\n" + "\n".join(inner) + "\n  end")
    blocks = "\n\n".join(parts)
    return f'''class Lokra < Formula
  desc "Lock rows for AI agents: a policy gate between AI agents and Postgres"
  homepage "https://lokra.dev"
  version "{version}"
  license "Apache-2.0"

{blocks}

  def install
    libexec.install Dir["*"]
    bin.install_symlink libexec/"lokra"
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/lokra --version")
  end
end
'''


def main() -> int:
    version, sums_file, *rest = sys.argv[1:]
    sums = {}
    for line in Path(sums_file).read_text(encoding="utf-8").splitlines():
        digest, name = line.split()
        sums[name.lstrip("*")] = digest
    sys.stdout.write(formula(version, sums, *rest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
