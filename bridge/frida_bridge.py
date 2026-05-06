"""
Frida Bridge — PC 端与 Root 手机上 frida-server 的通信层

工作原理：
  1. 手机通过 USB 连接 Mac
  2. frida-server 在手机上以 root 身份运行
  3. 本模块通过 frida Python API 连接到手机进程
  4. 注入 Hook 脚本，建立双向 RPC 通道
  5. Hook 脚本拦截的数据通过 on_message() 回调回流到 Python
"""

import frida
import json
import time
import threading
import queue
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

HOOKS_DIR = Path(__file__).parent.parent / "hooks"
AGENT_DIR  = Path(__file__).parent.parent / "frida_agent"  # 编译好的 frida-java-bridge agent

# 美团主包名（已确认：手机安装的是主 App 版，不是独立外卖版）
MEITUAN_PACKAGE = "com.sankuai.meituan"
MEITUAN_PACKAGE_ALT = "com.sankuai.meituan.takeoutnew"  # 独立外卖版（备用）


class FridaBridge:
    """
    Frida 通信桥接器
    封装与手机上 frida-server 的全部交互
    """

    def __init__(self, package: str = MEITUAN_PACKAGE):
        self.package = package
        self.device: Optional[frida.core.Device] = None
        self.session: Optional[frida.core.Session] = None
        self.script: Optional[frida.core.Script] = None

        # 消息队列：Hook 脚本发来的数据都放这里
        self._msg_queue: queue.Queue = queue.Queue()
        # 按 URL pattern 注册的监听器
        self._url_listeners: dict[str, list[Callable]] = {}
        # 通用消息监听器
        self._generic_listeners: list[Callable] = []

        self._connected = False
        self._spawn_pid: int = 0  # spawn 模式记录 pid，注入后 resume

    # ──────────────── 连接 ────────────────

    def connect(self, mode: str = "auto") -> "FridaBridge":
        """
        连接到 USB 设备上的 frida-server 或 frida-gadget

        Args:
            mode: 连接模式
                "auto"   - 自动尝试所有方式（推荐）
                "gadget" - 连接嵌入的 frida-gadget（重打包 APK 后使用）
                "spawn"  - 冷启动注入（需要 frida-server root）
                "pid"    - 用 PID 直接附加（绕过名称检测）
                "attach" - 按包名附加（最快但可能被检测）
        """
        logger.info("正在连接 USB 设备...")

        try:
            self.device = frida.get_usb_device(timeout=10)
        except frida.InvalidArgumentError:
            raise RuntimeError(
                "未找到 USB 设备。请确认：\n"
                "  1. 手机通过 USB 连接到 Mac\n"
                "  2. 手机已开启 USB 调试\n"
                "  3. frida-server 已在手机上以 root 运行（或已安装 gadget 版美团）"
            )

        logger.info(f"已连接设备: {self.device.name} ({self.device.id})")

        if mode == "auto":
            # 策略0：gadget 模式（最优先，重打包后无需 frida-server）
            try:
                logger.info("[gadget] 尝试连接 frida-gadget...")
                # 先确保美团在前台运行
                self._ensure_app_running()
                import time; time.sleep(2)
                self.session = self.device.attach("Gadget")
                self._connected = True
                logger.info("✓ gadget 模式成功（无需 frida-server，无反调试）")
                return self
            except Exception as e:
                logger.info(f"gadget 未激活（美团可能是原版）: {e}")

            # 策略1：spawn 模式（冷启动，最强）
            # 正确顺序: spawn → attach → [注入脚本] → resume
            for pkg in [self.package, MEITUAN_PACKAGE_ALT]:
                try:
                    logger.info(f"[spawn] 冷启动注入 {pkg}...")
                    pid = self.device.spawn([pkg])
                    self.session = self.device.attach(pid)
                    self.package = pkg
                    self._connected = True
                    self._spawn_pid = pid  # 记录 pid，注入完脚本后再 resume
                    logger.info(f"✓ spawn 模式成功: {pkg} (pid={pid})，等待注入脚本后 resume")
                    return self
                except Exception as e:
                    logger.warning(f"spawn 失败: {e}")

            # 策略2：PID 直接附加（绕过名称检测）
            pid = self._get_meituan_pid()
            if pid:
                try:
                    logger.info(f"[PID] 直接附加到 pid={pid}...")
                    self.session = self.device.attach(pid)
                    self._connected = True
                    logger.info(f"✓ PID 模式成功 (pid={pid})")
                    return self
                except Exception as e:
                    logger.warning(f"PID attach 失败: {e}")

            # 策略3：按包名附加
            self._ensure_app_running()
            for pkg in [self.package, MEITUAN_PACKAGE_ALT]:
                try:
                    logger.info(f"[attach] 按包名附加 {pkg}...")
                    self.session = self.device.attach(pkg)
                    self.package = pkg
                    self._connected = True
                    logger.info(f"✓ attach 模式成功: {pkg}")
                    return self
                except frida.ProcessNotFoundError:
                    logger.warning(f"{pkg} 按名称 attach 失败")

            raise RuntimeError(
                "所有连接策略均失败。\n"
                "建议：运行 python gadget_inject.py 重打包美团并安装 gadget 版本。"
            )

        elif mode == "gadget":
            # 直接连接 gadget（美团已重打包嵌入 frida-gadget）
            self._ensure_app_running()
            import time; time.sleep(3)
            self.session = self.device.attach("Gadget")
        elif mode == "spawn":
            pid = self.device.spawn([self.package])
            self.session = self.device.attach(pid)
            self.device.resume(pid)
        elif mode == "pid":
            pid = self._get_meituan_pid()
            if not pid:
                raise RuntimeError("未找到美团主进程，请先打开美团 App")
            self.session = self.device.attach(pid)
        else:  # attach
            self._ensure_app_running()
            self.session = self.device.attach(self.package)

        self._connected = True
        logger.info(f"已成功附加到美团进程: {self.package}")
        return self

    def _get_meituan_pid(self) -> int:
        """通过 ADB ps 获取美团主进程 PID（排除子进程）"""
        import subprocess
        for pkg in [self.package, MEITUAN_PACKAGE_ALT]:
            result = subprocess.run(
                ["adb", "shell", "ps", "-A"],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                # 主进程包名完全匹配（末尾列），不含冒号子进程名
                if len(parts) >= 9 and parts[-1] == pkg:
                    try:
                        return int(parts[1])
                    except ValueError:
                        pass
        return 0

    def _ensure_app_running(self):
        """检查美团是否在运行，未运行则通过 ADB 启动"""
        import subprocess
        for pkg in [self.package, MEITUAN_PACKAGE_ALT]:
            result = subprocess.run(
                ["adb", "shell", "pidof", pkg],
                capture_output=True, text=True
            )
            if result.stdout.strip():
                logger.info(f"美团已在运行: {pkg} (pid: {result.stdout.strip()})")
                return

        logger.info("美团未运行，正在启动...")
        subprocess.run([
            "adb", "shell", "monkey", "-p", self.package,
            "-c", "android.intent.category.LAUNCHER", "1"
        ], capture_output=True)
        time.sleep(4)  # 等待 App 启动

    # ──────────────── 注入脚本 ────────────────

    def inject_hooks(self):
        """
        注入全套 Hook 脚本。

        Frida 17 兼容说明：
          - Java bridge 在 Frida 17 中不再内置，需要通过 frida-java-bridge npm 包
          - 优先使用 frida_agent/_agent.js（已编译包含 frida-java-bridge）
          - gadget 模式：通过 TCP 27042 端口连接后注入
          - spawn 模式：spawn → attach → 注入 → resume
        """
        if not self._connected:
            raise RuntimeError("请先调用 connect()")

        # 优先使用编译好的 agent（含 frida-java-bridge，支持 Java bridge）
        compiled_agent = AGENT_DIR / "_agent.js"
        if compiled_agent.exists():
            hook_code = compiled_agent.read_text(encoding="utf-8")
            logger.info(f"使用编译 agent: {compiled_agent} ({len(hook_code)//1024}KB)")
        else:
            # 降级：使用原始 JS（可能无法激活 Java bridge）
            logger.warning("编译 agent 不存在，使用原始 meituan_hook.js（可能无 Java bridge）")
            hook_code = self._load_hook_scripts(["meituan_hook.js"])

        self.script = self.session.create_script(hook_code)
        self.script.on("message", self._on_message)
        self.script.load()

        # spawn 模式：脚本注入完成后 resume 进程
        if self._spawn_pid:
            logger.info(f"[spawn] 脚本已注入，resume 进程 (pid={self._spawn_pid})")
            self.device.resume(self._spawn_pid)
            self._spawn_pid = 0

        logger.info("✓ Hook 脚本注入完成")




    def _load_hook_scripts(self, filenames: list[str]) -> str:
        """加载并合并多个 Hook 脚本文件"""
        parts = []
        for filename in filenames:
            path = HOOKS_DIR / filename
            if path.exists():
                parts.append(f"// ====== {filename} ======")
                parts.append(path.read_text(encoding="utf-8"))
            else:
                logger.warning(f"Hook 脚本不存在: {path}")
        return "\n\n".join(parts)

    # ──────────────── 消息处理 ────────────────

    def _on_message(self, message: dict, data):
        """Frida 消息回调（在独立线程中调用）"""
        if message["type"] == "send":
            payload = message["payload"]
            self._msg_queue.put(payload)
            self._dispatch(payload)
        elif message["type"] == "error":
            logger.error(f"Hook 脚本错误: {message['description']}")
            logger.debug(f"Stack: {message.get('stack', '')}")

    def _dispatch(self, payload: dict):
        """将消息分发给注册的监听器"""
        url = payload.get("url", "")

        # 按 URL 模式分发
        for pattern, listeners in self._url_listeners.items():
            if pattern in url:
                for cb in listeners:
                    try:
                        cb(payload)
                    except Exception as e:
                        logger.error(f"监听器异常: {e}")

        # 通用监听器
        for cb in self._generic_listeners:
            try:
                cb(payload)
            except Exception as e:
                logger.error(f"通用监听器异常: {e}")

    # ──────────────── 监听器注册 ────────────────

    def on_url(self, url_pattern: str, callback: Callable):
        """注册 URL 模式监听器，当拦截到匹配 URL 时触发"""
        if url_pattern not in self._url_listeners:
            self._url_listeners[url_pattern] = []
        self._url_listeners[url_pattern].append(callback)

    def on_any(self, callback: Callable):
        """注册通用消息监听器"""
        self._generic_listeners.append(callback)

    # ──────────────── 同步等待 ────────────────

    def wait_for_url(
        self,
        url_pattern: str,
        timeout: float = 10.0
    ) -> Optional[dict]:
        """
        阻塞等待指定 URL 模式的响应
        用于：触发 App 操作后等待其 API 响应
        """
        result_event = threading.Event()
        result_holder = {}

        def on_match(payload):
            result_holder["data"] = payload
            result_event.set()

        self.on_url(url_pattern, on_match)

        if result_event.wait(timeout=timeout):
            # 清理监听器
            self._url_listeners[url_pattern].remove(on_match)
            return result_holder["data"]
        else:
            logger.warning(f"等待 {url_pattern} 超时 ({timeout}s)")
            return None

    # ──────────────── RPC 调用 ────────────────

    def rpc_call(self, method: str, *args) -> any:
        """
        调用 Hook 脚本中暴露的 RPC 函数
        用于主动注入操作（点击、填写等）
        """
        if not self.script:
            raise RuntimeError("Hook 脚本未注入")
        exports = self.script.exports_sync
        return getattr(exports, method)(*args)

    # ──────────────── 清理 ────────────────

    def disconnect(self):
        """断开连接，清理资源"""
        if self.script:
            try:
                self.script.unload()
            except Exception:
                pass
        if self.session:
            try:
                self.session.detach()
            except Exception:
                pass
        self._connected = False
        logger.info("Frida Bridge 已断开")

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.disconnect()
