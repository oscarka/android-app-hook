#!/usr/bin/env python3
"""
美团 APK 重打包脚本
嵌入 frida-gadget.so，绕过美团的 Frida 反检测

流程：
  1. apktool 反编译 APK
  2. 把 frida-gadget.so 复制到 lib/arm64-v8a/
  3. 在 Application 入口的 smali 中注入 System.loadLibrary("frida-gadget")
  4. apktool 重新打包
  5. zipalign 对齐
  6. apksigner 签名（用自生成 keystore）
  7. adb install 安装
"""

import os
import sys
import shutil
import subprocess
import glob
from pathlib import Path

# ─── 路径配置 ───────────────────────────────────────────────────────────────────
WORKSPACE   = Path(__file__).parent / "gadget_workspace"
ORIG_APK    = WORKSPACE / "meituan_orig.apk"
GADGET_SO   = WORKSPACE / "frida-gadget.so"
DECOMPILE_DIR = WORKSPACE / "meituan_decompiled"
PATCHED_APK = WORKSPACE / "meituan_patched_unsigned.apk"
ALIGNED_APK = WORKSPACE / "meituan_patched_aligned.apk"
SIGNED_APK  = WORKSPACE / "meituan_patched_signed.apk"
KEYSTORE    = WORKSPACE / "debug.keystore"

KEYTOOL = Path("/opt/homebrew/Cellar/openjdk@11/11.0.28/bin/keytool")
if not KEYTOOL.exists():
    KEYTOOL = Path("/opt/homebrew/Cellar/openjdk@21/21.0.8/bin/keytool")
if not KEYTOOL.exists():
    KEYTOOL = Path("/usr/bin/keytool")

SDK_BUILD_TOOLS = Path.home() / "Library/Android/sdk/build-tools/36.1.0"
ZIPALIGN  = SDK_BUILD_TOOLS / "zipalign"
APKSIGNER = SDK_BUILD_TOOLS / "apksigner"

# ─── 工具函数 ────────────────────────────────────────────────────────────────────

def run(cmd, **kwargs):
    """运行命令并打印输出"""
    print(f"\n▶ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.stdout:
        print(result.stdout[:2000])
    if result.returncode != 0:
        print(f"[STDERR] {result.stderr[:1000]}")
        raise RuntimeError(f"命令失败 (exit={result.returncode})")
    return result

def check_tool(name, path=None):
    """检查工具是否可用"""
    tool = path or name
    if not shutil.which(str(tool)):
        raise RuntimeError(f"❌ 工具未找到: {tool}\n  请先安装: brew install {name}")
    print(f"✓ {name}: {shutil.which(str(tool))}")

# ─── 步骤 1：检查工具 ─────────────────────────────────────────────────────────────

def step1_check_tools():
    print("\n═══ 步骤 1：检查工具链 ═══")
    check_tool("apktool")
    if not ZIPALIGN.exists():
        raise RuntimeError(f"❌ zipalign 未找到: {ZIPALIGN}")
    if not APKSIGNER.exists():
        raise RuntimeError(f"❌ apksigner 未找到: {APKSIGNER}")
    if not ORIG_APK.exists():
        raise RuntimeError(f"❌ 原始 APK 未找到: {ORIG_APK}")
    if not GADGET_SO.exists():
        raise RuntimeError(f"❌ frida-gadget.so 未找到: {GADGET_SO}")
    print(f"✓ zipalign: {ZIPALIGN}")
    print(f"✓ apksigner: {APKSIGNER}")
    print(f"✓ APK: {ORIG_APK} ({ORIG_APK.stat().st_size // 1024 // 1024} MB)")
    print(f"✓ gadget: {GADGET_SO} ({GADGET_SO.stat().st_size // 1024 // 1024} MB)")

# ─── 步骤 2：反编译 APK ───────────────────────────────────────────────────────────

def step2_decompile():
    print("\n═══ 步骤 2：反编译 APK ═══")
    if DECOMPILE_DIR.exists():
        print(f"已存在反编译目录，清理中: {DECOMPILE_DIR}")
        shutil.rmtree(DECOMPILE_DIR)
    # 重要：保留 --no-res！完整解码资源会导致资源 ID 错位，App 运行时滚 Crash
    # Application 类名通过 aapt dump 直接从原始 APK 读取，不需要解码 Manifest
    run(["apktool", "d", str(ORIG_APK), "-o", str(DECOMPILE_DIR), "--no-res", "-f"])
    print(f"✓ 反编译完成: {DECOMPILE_DIR}")

# ─── 步骤 3：嵌入 frida-gadget.so ────────────────────────────────────────────────

def step3_embed_gadget():
    print("\n═══ 步骤 3：嵌入 frida-gadget.so ═══")

    # ⚠ 关键：原始 APK 只有 armeabi（32位），没有 arm64-v8a
    # 如果我们创建 arm64-v8a 目录，Android 会优先使用它，
    # 但里面缺少所有必需的 so，导致 App 崩溃（NoClassDefFoundError/UnsatisfiedLinkError）
    # 解决方案：把 gadget 放进 armeabi，删除 arm64-v8a 目录

    # 删除我们之前可能创建的 arm64-v8a 目录
    arm64_dir = DECOMPILE_DIR / "lib" / "arm64-v8a"
    if arm64_dir.exists():
        shutil.rmtree(arm64_dir)
        print("✓ 已删除 arm64-v8a 目录（避免 ABI 切换导致 so 缺失）")

    # 放进 armeabi（与 App 原始 ABI 一致）
    lib_dir = DECOMPILE_DIR / "lib" / "armeabi"
    lib_dir.mkdir(parents=True, exist_ok=True)

    # 优先使用 armeabi 版 gadget（arm32），备用 arm64 版
    armeabi_gadget = WORKSPACE / "frida-gadget-armeabi.so"
    gadget_src = armeabi_gadget if armeabi_gadget.exists() else GADGET_SO
    print(f"使用 gadget: {gadget_src.name} ({gadget_src.stat().st_size//1024//1024}MB)")

    gadget_dst = lib_dir / "libfrida-gadget.so"
    shutil.copy2(gadget_src, gadget_dst)
    print(f"✓ gadget 已复制: {gadget_dst}")

    # gadget 配置文件：listen 模式，on_load=resume 让 App 正常启动
    gadget_config = lib_dir / "libfrida-gadget.config.so"
    gadget_config.write_text('{\n  "interaction": {\n    "type": "listen",\n    "address": "127.0.0.1",\n    "port": 27042,\n    "on_load": "resume"\n  }\n}')
    print(f"✓ gadget 配置已写入: {gadget_config}")



# ─── 步骤 4：注入 System.loadLibrary smali ──────────────────────────────────────

def step4_patch_smali():
    """
    在 MeituanApplication.attachBaseContext() 最开头注入:
        const-string v0, "frida-gadget"
        invoke-static {v0}, Ljava/lang/System;->loadLibrary(...)V

    策略（按优先级）：
      1. 用 aapt dump 从原始 APK 读取 Application 类名（最可靠）
      2. 读取 --no-res 解码的二进制 Manifest（可能失败）
      3. 搜索含有 attachBaseContext 的 Application 子类 smali
    注入点优先 attachBaseContext（最早执行），次选 onCreate
    """
    print("\n═══ 步骤 4：注入 System.loadLibrary smali ═══")

    import re
    import subprocess

    app_class = None

    # 策略1：用 aapt 直接从原始 APK 读取 Application 类名（不受 --no-res 影响）
    sdk_build_tools = Path.home() / "Library/Android/sdk/build-tools/36.1.0"
    aapt = sdk_build_tools / "aapt"
    if aapt.exists():
        try:
            result = subprocess.run(
                [str(aapt), "dump", "xmltree", str(ORIG_APK), "AndroidManifest.xml"],
                capture_output=True, text=True, timeout=30
            )
            in_app = False
            for line in result.stdout.splitlines():
                if "E: application" in line:
                    in_app = True
                if in_app and "android:name" in line:
                    m = re.search(r'"([^"]+)"', line)
                    if m:
                        app_class = m.group(1)
                        break
                if in_app and "E: activity" in line:
                    break
            if app_class:
                print(f"策略1 (aapt dump) 找到 Application 类: {app_class}")
        except Exception as e:
            print(f"aapt dump 失败: {e}")
    else:
        print("未找到 aapt，跳过策略1")

    # 策略2：尝试读取 Manifest（--no-res 模式下是二进制，可能失败）
    if not app_class:
        manifest_path = DECOMPILE_DIR / "AndroidManifest.xml"
        try:
            manifest_text = manifest_path.read_text(encoding="utf-8")
            m = re.search(r'<application[^>]+android:name="([^"]+)"', manifest_text, re.DOTALL)
            if m:
                app_class = m.group(1)
                print(f"策略2 (Manifest XML) 找到: {app_class}")
        except Exception as e:
            print(f"Manifest 读取失败（二进制格式，符合预期）: {e}")

    injected = False

    # 精确定位 Application 类 smali，优先 attachBaseContext
    if app_class:
        class_path = app_class.strip().lstrip(".").replace(".", "/")
        smali_candidates = list(DECOMPILE_DIR.glob(f"smali*/{class_path}.smali"))
        print(f"  搜索 {class_path}.smali -> {len(smali_candidates)} 个匹配")
        for sf in smali_candidates:
            # 优先注入 attachBaseContext（最早执行）
            if _inject_at_method(sf, "attachBaseContext"):
                injected = True
                break
        if not injected:
            for sf in smali_candidates:
                if _inject_at_method(sf, "onCreate"):
                    injected = True
                    break

    # 备用：搜索继承 Application 的 smali
    if not injected:
        print("备用：搜索含 attachBaseContext 的 Application smali...")
        for sf in DECOMPILE_DIR.glob("smali*/**/*.smali"):
            try:
                content = sf.read_text(encoding="utf-8", errors="ignore")
                if "attachBaseContext" in content and (
                    ".super Landroid/app/Application;" in content
                    or ".super Lcom/meituan" in content
                    or ".super Lcom/sankuai" in content
                ):
                    print(f"  尝试: {sf.relative_to(DECOMPILE_DIR)}")
                    if _inject_at_method(sf, "attachBaseContext"):
                        injected = True
                        break
            except Exception:
                pass

    if not injected:
        raise RuntimeError(
            "❌ 无法找到合适的注入点！\n"
            "请手动检查 smali 目录找到 Application 子类。"
        )


def _inject_smali_file(smali_path: Path) -> bool:
    """在 smali 文件的 onCreate 方法最开头注入 loadLibrary"""
    return _inject_at_method(smali_path, "onCreate")


def _inject_at_method(smali_path: Path, method_name: str) -> bool:
    """在指定方法最开头注入 System.loadLibrary("frida-gadget")"""
    import re

    try:
        content = smali_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"  读取失败: {e}")
        return False

    # 检查是否已注入
    if "frida-gadget" in content:
        print(f"  ⚠ 已注入过: {smali_path.name}")
        return True

    # 注入代码：loadLibrary 需要一个寄存器
    inject_lines = (
        '\n    # === frida-gadget injection start ===\n'
        '    const-string v0, "frida-gadget"\n'
        '    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n'
        '    # === frida-gadget injection end ===\n'
    )

    # 在 .method ... methodName ... 后找到 .locals N，在之后插入
    pattern = (
        r'(\.method\s+(?:public\s+|protected\s+|private\s+|final\s+|bridge\s+)*'
        + re.escape(method_name) +
        r'\s*\([^)]*\)[^\n]*\n'
        r'(?:[ \t]*(?:\.annotation|\.param|\.prologue)[^\n]*\n(?:[^\n]*\n)*?)*?'
        r'[ \t]*\.locals\s+\d+)'
    )
    match = re.search(pattern, content, re.MULTILINE)

    if match:
        # 检查 .locals 寄存器数量（需要至少 v0）
        locals_match = re.search(r'\.locals\s+(\d+)', match.group(0))
        if locals_match and int(locals_match.group(1)) == 0:
            # 需要 v0，把 .locals 0 改成 .locals 1
            new_match_text = match.group(0).replace('.locals 0', '.locals 1')
            content = content[:match.start()] + new_match_text + content[match.end():]
            # 重新搜索更新后的位置
            match = re.search(pattern, content, re.MULTILINE)

        insert_pos = match.end()
        new_content = content[:insert_pos] + inject_lines + content[insert_pos:]
        smali_path.write_text(new_content, encoding="utf-8")
        print(f"  ✓ 注入成功 ({method_name}): {smali_path.relative_to(DECOMPILE_DIR)}")
        return True
    else:
        # 简化匹配：直接找方法名后的 .locals
        simple_pattern = (
            r'(\.method[^\n]*' + re.escape(method_name) + r'[^\n]*\n'
            r'(?:[ \t]*[^\n]*\n)*?'
            r'[ \t]*\.locals\s+\d+)'
        )
        match = re.search(simple_pattern, content)
        if match:
            insert_pos = match.end()
            new_content = content[:insert_pos] + inject_lines + content[insert_pos:]
            smali_path.write_text(new_content, encoding="utf-8")
            print(f"  ✓ 注入成功(简化匹配) ({method_name}): {smali_path.relative_to(DECOMPILE_DIR)}")
            return True
        print(f"  ⚠ 未找到 {method_name} 方法: {smali_path.name}")
        return False

# ─── 步骤 5：重新打包 APK ─────────────────────────────────────────────────────────

def step5_repackage():
    print("\n═══ 步骤 5：重新打包 APK ═══")
    if PATCHED_APK.exists():
        PATCHED_APK.unlink()
    run(["apktool", "b", str(DECOMPILE_DIR), "-o", str(PATCHED_APK)])
    print(f"✓ 重打包完成: {PATCHED_APK}")

# ─── 步骤 6：zipalign 对齐 ────────────────────────────────────────────────────────

def step6_zipalign():
    print("\n═══ 步骤 6：zipalign 对齐 ═══")
    if ALIGNED_APK.exists():
        ALIGNED_APK.unlink()
    run([str(ZIPALIGN), "-v", "4", str(PATCHED_APK), str(ALIGNED_APK)])
    print(f"✓ 对齐完成: {ALIGNED_APK}")

# ─── 步骤 7：生成签名 & 签名 APK ──────────────────────────────────────────────────

def step7_sign():
    print("\n═══ 步骤 7：签名 ═══")

    # 生成调试用 keystore（如果不存在）
    if not KEYSTORE.exists():
        print("生成调试 keystore...")
        run([
            str(KEYTOOL), "-genkeypair",
            "-v", "-keystore", str(KEYSTORE),
            "-alias", "debug",
            "-keyalg", "RSA", "-keysize", "2048",
            "-validity", "10000",
            "-storepass", "android",
            "-keypass", "android",
            "-dname", "CN=Debug, OU=Debug, O=Debug, L=Debug, S=Debug, C=US"
        ])
        print(f"✓ keystore 生成: {KEYSTORE}")

    if SIGNED_APK.exists():
        SIGNED_APK.unlink()

    run([
        str(APKSIGNER), "sign",
        "--ks", str(KEYSTORE),
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--ks-key-alias", "debug",
        "--out", str(SIGNED_APK),
        str(ALIGNED_APK)
    ])
    print(f"✓ 签名完成: {SIGNED_APK} ({SIGNED_APK.stat().st_size // 1024 // 1024} MB)")

# ─── 步骤 8：安装到手机 ────────────────────────────────────────────────────────────

def step8_install():
    print("\n═══ 步骤 8：安装到手机 ═══")
    print("⚠ 注意：需要先卸载原版美团（签名不同无法覆盖安装）")

    # 卸载原版
    print("卸载原版美团...")
    subprocess.run(["adb", "uninstall", "com.sankuai.meituan"],
                   capture_output=True, text=True)

    # 安装重打包版
    run(["adb", "install", "-r", "-t", str(SIGNED_APK)])
    print("✓ 安装完成！")
    print()
    print("现在可以直接运行:")
    print("  python cli/main.py ping")
    print("  python cli/main.py sniff")
    print()
    print("frida-gadget 会在美团启动时自动监听 27042 端口，")
    print("Python 端通过 frida.get_usb_device() 无需 frida-server 即可连接。")

# ─── 主流程 ──────────────────────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║  美团 APK 重打包 — 嵌入 frida-gadget                ║")
    print("╚══════════════════════════════════════════════════════╝")

    steps = [
        ("工具检查",     step1_check_tools),
        ("反编译",       step2_decompile),
        ("嵌入 gadget", step3_embed_gadget),
        ("注入 smali",  step4_patch_smali),
        ("重打包",       step5_repackage),
        ("zipalign",    step6_zipalign),
        ("签名",         step7_sign),
        ("安装",         step8_install),
    ]

    start_step = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    for i, (name, fn) in enumerate(steps, 1):
        if i < start_step:
            print(f"[跳过] 步骤 {i}: {name}")
            continue
        try:
            fn()
        except Exception as e:
            print(f"\n❌ 步骤 {i} ({name}) 失败: {e}")
            print(f"\n提示：修复问题后可从步骤 {i} 继续:")
            print(f"  python gadget_inject.py {i}")
            sys.exit(i)

    print("\n╔══════════════════════════════════════════════════════╗")
    print("║  ✅ 全部步骤完成！美团已植入 frida-gadget            ║")
    print("╚══════════════════════════════════════════════════════╝")

if __name__ == "__main__":
    main()
