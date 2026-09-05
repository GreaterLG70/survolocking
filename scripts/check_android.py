"""
Android 工程静态一致性校验。

在没有 Android SDK / Kotlin 编译器的环境下，尽可能提前发现：
1. binding.xxx 引用了 layout 中不存在的 id；
2. R.string / R.drawable / R.layout 引用了不存在的资源；
3. AndroidManifest 中声明的组件找不到对应 Kotlin 源文件；
4. layout 中 @string / @drawable 引用了不存在的资源。

运行： python scripts/check_android.py
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "android"
RES = ROOT / "app/src/main/res"
KT = ROOT / "app/src/main/kotlin"

errors: list[str] = []
warnings: list[str] = []


def camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def collect_strings() -> set[str]:
    out: set[str] = set()
    for f in (RES / "values").glob("*.xml"):
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError as e:
            errors.append(f"[XML解析失败] {f.name}: {e}")
            continue
        for el in root.iter("string"):
            name = el.attrib.get("name")
            if name:
                out.add(name)
    return out


def collect_ids(layout_file: Path) -> set[str]:
    out: set[str] = set()
    try:
        root = ET.parse(layout_file).getroot()
    except ET.ParseError as e:
        errors.append(f"[XML解析失败] {layout_file.name}: {e}")
        return out
    for el in root.iter():
        rid = el.attrib.get("{http://schemas.android.com/apk/res/android}id")
        if rid:
            out.add(rid.replace("@+id/", "").replace("@id/", ""))
    return out


def collect_layout_refs(layout_file: Path) -> tuple[set[str], set[str]]:
    """返回该 layout 中引用的 @string 与 @drawable 名称集合。"""
    strings: set[str] = set()
    drawables: set[str] = set()
    text = layout_file.read_text(encoding="utf-8")
    strings |= set(re.findall(r'"@string/(\w+)"', text))
    drawables |= set(re.findall(r'"@drawable/(\w+)"', text))
    return strings, drawables


def main() -> int:
    if not ROOT.exists():
        print("未找到 android 目录")
        return 1

    strings = collect_strings()
    layouts = {f.stem: f for f in (RES / "layout").glob("*.xml")}
    # R.id 可能定义在 layout 或 menu（BottomNavigationView 菜单项）中
    id_sources = list(layouts.values()) + list((RES / "menu").glob("*.xml"))
    drawables = set()
    for d in ("drawable", "drawable-v24", "drawable-nodpi", "mipmap-anydpi-v26"):
        if (RES / d).exists():
            for f in (RES / d).glob("*.xml"):
                drawables.add(f.stem)
            for f in (RES / d).glob("*.webp"):
                drawables.add(f.stem)
            for f in (RES / d).glob("*.png"):
                drawables.add(f.stem)

    # -------------------------------------------------- 检查 Kotlin 文件
    kt_files = list(KT.rglob("*.kt"))
    print(f"扫描 {len(kt_files)} 个 Kotlin 文件，{len(layouts)} 个 layout，{len(strings)} 个 string")

    for f in kt_files:
        src = f.read_text(encoding="utf-8")
        rel = f.relative_to(ROOT)

        # binding 字段 → 对应 layout 的 id
        for binding_var in re.findall(r"\b(\w+Binding)\b", src):
            base = binding_var.replace("Binding", "")
            layout_name = camel_to_snake(base)
            layout_path = layouts.get(layout_name)
            if layout_path is None:
                errors.append(f"[{rel}] 使用 {binding_var}，但找不到 res/layout/{layout_name}.xml")
                continue
            ids = collect_ids(layout_path)
            for field in re.findall(rf"\b{re.escape(binding_var[:1].lower() + binding_var[1:] if False else 'binding')}\.(\w+)", src):
                if field in {"root", "inflate"}:
                    continue
                # ViewBinding 会把 snake_case id（tv_nickname）映射为 camelCase
                # 属性（tvNickname），两种形式都算命中
                if field not in ids and camel_to_snake(field) not in ids:
                    errors.append(
                        f"[{rel}] binding.{field} 在 {layout_path.name} 中未定义"
                    )

        # R.string.xxx（android.R.string.* 是框架资源，跳过）
        for name in re.findall(r"(?<!android\.)R\.string\.(\w+)", src):
            if name not in strings:
                errors.append(f"[{rel}] R.string.{name} 未定义")

        # R.layout.xxx
        for name in re.findall(r"(?<!android\.)R\.layout\.(\w+)", src):
            if name not in layouts:
                errors.append(f"[{rel}] R.layout.{name} 未定义")

        # R.drawable.xxx
        for name in re.findall(r"(?<!android\.)R\.drawable\.(\w+)", src):
            if name not in drawables:
                errors.append(f"[{rel}] R.drawable.{name} 未定义")

        # R.id.xxx
        for name in re.findall(r"(?<!android\.)R\.id\.(\w+)", src):
            found = any(name in collect_ids(p) for p in id_sources)
            if not found:
                errors.append(f"[{rel}] R.id.{name} 在任何 layout/menu 中均未定义")

    # -------------------------------------------------- 检查 layout 引用
    for name, path in layouts.items():
        s, d = collect_layout_refs(path)
        for x in s:
            if x not in strings:
                errors.append(f"[{path.name}] @string/{x} 未定义")
        for x in d:
            if x not in drawables:
                errors.append(f"[{path.name}] @drawable/{x} 未定义")

    # -------------------------------------------------- 检查 Manifest 组件
    manifest = ROOT / "app/src/main/AndroidManifest.xml"
    if manifest.exists():
        mroot = ET.parse(manifest).getroot()
        ns = "{http://schemas.android.com/apk/res/android}"
        package = "com.survolocking"
        for tag in ("activity", "service", "receiver", "application"):
            for el in mroot.iter(tag):
                cls = el.attrib.get(f"{ns}name", "")
                if not cls:
                    continue
                # 以 . 开头是相对包名写法，需补全为全限定名
                full = f"{package}{cls}" if cls.startswith(".") else f"{package}.{cls}"
                rel_path = full.replace(".", "/") + ".kt"
                if not (KT / rel_path).exists():
                    errors.append(f"[AndroidManifest] <{tag}> {cls} 找不到源文件 {rel_path}")
        # 检查 icon / theme / 资源引用
        text = manifest.read_text(encoding="utf-8")
        for m in re.findall(r"@drawable/(\w+)", text):
            if m not in drawables:
                errors.append(f"[AndroidManifest] @drawable/{m} 未定义")
        for m in re.findall(r"@string/(\w+)", text):
            if m not in strings:
                errors.append(f"[AndroidManifest] @string/{m} 未定义")
        for m in re.findall(r"@xml/(\w+)", text):
            if not (RES / "xml" / f"{m}.xml").exists():
                errors.append(f"[AndroidManifest] @xml/{m} 未定义")
        # 样式名可含点号，如 Theme.Survolocking
        for m in re.findall(r"@style/([\w.]+)", text):
            theme_files = list((RES / "values").glob("themes.xml"))
            found = False
            for tf in theme_files:
                if f'name="{m}"' in tf.read_text(encoding="utf-8"):
                    found = True
                    break
            if not found:
                errors.append(f"[AndroidManifest] @style/{m} 未定义")

    # -------------------------------------------------- 输出
    print()
    if errors:
        print(f"发现 {len(errors)} 个问题：")
        for e in errors:
            print(f"  [错误] {e}")
    else:
        print("静态校验通过，未发现引用错误")
    if warnings:
        print()
        for w in warnings:
            print(f"  [警告] {w}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
