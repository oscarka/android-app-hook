#!/usr/bin/env python3
"""
美团 API Hook 一键启动脚本
自动处理 DeprecatedAbiDialog，注入 frida agent，监听 API
"""

import frida
import time
import json
import subprocess
import sys
from pathlib import Path

AGENT_JS = Path(__file__).parent / "frida_agent" / "_agent.js"
FRIDA_SERVER = "/data/local/tmp/frida-server"
PACKAGE = "com.sankuai.meituan"
MAIN_ACTIVITY = "com.meituan.android.pt.homepage.activity.MainActivity"


def adb(*args):
    result = subprocess.run(["adb", "shell"] + list(args),
                            capture_output=True, text=True)
    return result.stdout.strip()


def adb_bg(*args):
    subprocess.Popen(["adb", "shell"] + list(args),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_frida_server():
    print("🔧 启动 frida-server...")
    adb_bg(f"su -c 'pkill -f frida-server 2>/dev/null; sleep 0.5; {FRIDA_SERVER} &'")
    time.sleep(2)
    status = adb(f"su -c 'ss -tlnp | grep 27042'")
    if "27042" in status:
        print("  ✓ frida-server 已就绪")
        return True
    print("  ⚠ frida-server 启动失败")
    return False


def dismiss_deprecated_abi_dialog():
    """点击 DeprecatedAbiDialog 的确认按钮"""
    for _ in range(5):
        focus = adb("dumpsys window | grep mCurrentFocus | head -1")
        if "DeprecatedAbiDialog" in focus:
            print("  📱 检测到 ABI 兼容性对话框，自动关闭...")
            adb("input tap 540 1800")  # 点击对话框按钮（通常在底部）
            time.sleep(1)
            return True
        time.sleep(1)
    return False


def get_meituan_pid():
    result = subprocess.run(["adb", "shell", "ps", "-A"],
                            capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.strip().endswith(PACKAGE):
            return int(line.split()[1])
    return None


def run_hook(duration: int = 120):
    agent_js = AGENT_JS.read_text(encoding="utf-8")

    # 1. 启动 frida-server
    if not start_frida_server():
        print("请手动启动 frida-server 后重试")
        sys.exit(1)

    # 2. 停止并重新启动美团
    print(f"\n🚀 启动 {PACKAGE}...")
    adb(f"am force-stop {PACKAGE}")
    time.sleep(1)
    adb(f"am start -n '{PACKAGE}/{MAIN_ACTIVITY}'")
    time.sleep(2)

    # 3. 自动关掉 DeprecatedAbiDialog
    dismiss_deprecated_abi_dialog()

    # 4. 等待主界面出现
    print("⏳ 等待主界面加载...")
    deadline = time.time() + 15
    while time.time() < deadline:
        focus = adb("dumpsys window | grep mCurrentFocus | head -1")
        if PACKAGE in focus and "DeprecatedAbiDialog" not in focus:
            print(f"  ✓ 主界面已获焦点")
            break
        if "DeprecatedAbiDialog" in focus:
            dismiss_deprecated_abi_dialog()
        time.sleep(1)

    # 5. 获取 PID 并 attach
    pid = get_meituan_pid()
    if not pid:
        print("❌ 未找到美团进程")
        sys.exit(1)

    print(f"\n🔌 Attach PID={pid}...")
    device = frida.get_usb_device()
    session = device.attach(pid)

    # 6. 注入 agent
    messages = []

    def on_msg(m, d):
        if m.get("type") == "send":
            p = m.get("payload", {})
            messages.append(p)
            url = p.get("url", "")
            if url:
                ts = time.strftime("%H:%M:%S")
                print(f"  [{ts}] {p.get('method','?')} {url[:80]}")
        elif m.get("type") == "error":
            desc = m.get("description", "")
            if "access-violation" not in desc:
                print(f"  ERR: {desc[:150]}")

    script = session.create_script(agent_js)
    script.on("message", on_msg)
    script.load()
    print("✅ Agent 注入成功！")

    time.sleep(1)
    try:
        result = script.exports_sync.ping()
        print(f"   ping: java_available={result.get('java_available')}")
    except Exception as e:
        print(f"   ping 失败: {e}")

    print(f"\n📡 监听 {duration} 秒，请在手机上操作美团...\n")
    start = time.time()
    while time.time() - start < duration:
        time.sleep(5)
        elapsed = int(time.time() - start)
        api_count = sum(1 for m in messages if m.get("url"))
        sys.stdout.write(f"\r  [{elapsed}s] 已捕获 {api_count} 条 API")
        sys.stdout.flush()

    print(f"\n\n📊 共捕获 {sum(1 for m in messages if m.get('url'))} 条 API 消息")
    for m in messages[:10]:
        if m.get("url"):
            print(f"  {m['method']} {m['url']}")

    session.detach()
    return messages


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    run_hook(duration)
