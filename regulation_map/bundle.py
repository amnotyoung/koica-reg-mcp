"""Inline the built regulation map into one self-contained HTML file.

Run after build.py:  python3 regulation_map/bundle.py [--out PATH]
The result needs no server, CDN or API and works from GitHub Pages, a static
host or a local file. Script order and `defer` timing are preserved by placing
the scripts, in their original order, at the end of <body>.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "dist" / "index.html"
STYLE_LINK = '<link rel="stylesheet" href="styles.css">'
BUNDLE_META = '<meta name="regulation-map-bundle" content="single-html">'
SCRIPT_TAGS = re.compile(r'<script defer src="([^"]+)"></script>')


def inline_safe(text: str, closing: str, name: str) -> str:
    # An inline block ends at the first closing tag, so the source must not contain one.
    if re.search(rf"</{closing}", text, re.I):
        raise ValueError(f"{name}에 </{closing}가 있어 인라인할 수 없습니다")
    return text


def bundle(root: Path = HERE) -> str:
    html = (root / "index.html").read_text(encoding="utf-8")
    if not (root / "data.js").exists():
        raise FileNotFoundError("data.js가 없습니다. python3 regulation_map/build.py를 먼저 실행하세요.")
    if html.count(STYLE_LINK) != 1:
        raise ValueError("index.html의 styles.css 링크를 찾지 못했습니다")
    css = inline_safe((root / "styles.css").read_text(encoding="utf-8"), "style", "styles.css")
    # Marks the single-file build: only this page offers to download itself.
    html = html.replace(STYLE_LINK, f'{BUNDLE_META}\n<style>\n{css}\n</style>')
    scripts = SCRIPT_TAGS.findall(html)
    if scripts != ["data.js", "map_model.js", "reader.js", "law_map.js", "app.js"]:
        raise ValueError(f"예상하지 못한 스크립트 구성입니다: {scripts}")
    html = SCRIPT_TAGS.sub("", html)
    inline = "".join(
        f"<script>\n{inline_safe((root / name).read_text(encoding='utf-8'), 'script', name)}\n</script>\n"
        for name in scripts
    )
    if html.count("</body>") != 1:
        raise ValueError("index.html의 </body>를 찾지 못했습니다")
    return html.replace("</body>", inline + "</body>")


def main() -> None:
    parser = argparse.ArgumentParser(description="규정 지도를 단일 HTML로 묶습니다")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(bundle(), encoding="utf-8")
    print(f"단일 HTML 생성: {args.out} ({args.out.stat().st_size / 1_000_000:.1f}MB)")


if __name__ == "__main__":
    main()
