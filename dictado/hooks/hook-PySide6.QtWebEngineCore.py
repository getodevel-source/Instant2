"""PyInstaller 6.22.3 QtWebEngine hook with DevTools resources omitted.

Keep in sync with PyInstaller's upstream hook when its pinned release changes.
Only the Chromium DevTools front-end bundles are omitted: Instant does not
expose DevTools, and these two resources account for about 83 MiB.
"""
from pathlib import Path

from PyInstaller.utils.hooks.qt import \
    add_qt6_dependencies, pyside6_library_info


if pyside6_library_info.version is not None:
    if pyside6_library_info.version < [6, 2, 2]:
        raise SystemExit(
            "ERROR: PyInstaller's QtWebEngine support requires Qt6 6.2.2 or later!")

    hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
    webengine_binaries, webengine_datas = \
        pyside6_library_info.collect_qtwebengine_files()

    kept_datas = []
    omitted_devtools = []
    for source, destination in webengine_datas:
        source_path = Path(source)
        if source_path.is_dir():
            children = tuple(source_path.rglob("*"))
            devtools = tuple(
                child for child in children
                if child.is_file()
                and child.name.startswith("qtwebengine_devtools_resources"))
            if devtools:
                omitted_devtools.extend(str(child) for child in devtools)
                for child in children:
                    if child.is_file() and child not in devtools:
                        relative_parent = child.relative_to(source_path).parent
                        kept_datas.append((
                            str(child),
                            str(Path(destination) / relative_parent),
                        ))
            else:
                kept_datas.append((source, destination))
        elif source_path.name.startswith("qtwebengine_devtools_resources"):
            omitted_devtools.append(source)
        else:
            kept_datas.append((source, destination))

    if not omitted_devtools:
        raise RuntimeError(
            "Expected QtWebEngine DevTools resources to omit, but none were found.")

    binaries += webengine_binaries
    datas += kept_datas
    hiddenimports += ["PySide6.QtPrintSupport"]
