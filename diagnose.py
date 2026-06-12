"""诊断脚本：输出微信 4.x 的 UIA 控件树，帮助理解 UI 结构。"""
import uiautomation as auto

root = auto.GetRootControl()
for w in root.GetChildren():
    if "微信" in w.Name or "WeChat" in w.Name or w.ClassName in ("Qt51514QWindowIcon", "CefTopWindow"):
        print(f"窗口: '{w.Name}' ClassName={w.ClassName}")

        # 输出前 4 层
        def dump(ctrl, depth=0):
            if depth > 6:
                return
            try:
                pad = "  " * depth
                name = (ctrl.Name or "")[:50]
                ct = ctrl.ControlTypeName
                cls = ctrl.ClassName or ""
                vp = ""
                try:
                    if hasattr(ctrl, 'IsValuePatternAvailable') and ctrl.IsValuePatternAvailable:
                        vp = " [V]"
                except Exception:
                    pass
                if name or ct:
                    print(f"{pad}{ct} '{name}' {vp} cls={cls}")
                for child in ctrl.GetChildren():
                    dump(child, depth + 1)
            except Exception:
                pass

        dump(w)
        break
