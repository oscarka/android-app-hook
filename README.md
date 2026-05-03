# android-app-hook 🔬

> **Android App 逆向 Hook 框架**
> 通过 Frida 注入拦截 App 内部 API，将任意 Android App 的业务数据提取为结构化 Python 对象。
> 当前以美团外卖为参考实现，设计上支持扩展到任意 App（抖音、饿了么、淘宝等）。

> ⚠️ **状态**：实验性 / WIP。UI 自动化路线（[meituan-cli](https://github.com/你的用户名/meituan-cli)）已可生产使用，本项目为 Hook 路线的探索性实现。

---

## 架构设计

```
AI Agent / CLI
      │
      ▼
  cli/main.py              # typer CLI，统一入口
      │
      ▼
  driver/base_driver.py    # 抽象 BaseDriver（可扩展到任意 App）
      │
      ▼
  driver/meituan_driver.py # 美团实现：通过 FridaBridge 拦截 API
      │
      ▼
  bridge/frida_bridge.py   # Frida WebSocket Bridge
      │
      ▼
  hooks/meituan_hook.js    # 注入 App 进程的 JS Hook 脚本
      │
      ▼
  美团 App（已注入 Frida）
```

---

## 核心组件

### `driver/base_driver.py` — 通用 Driver 抽象

所有 App Driver 必须实现的接口，保证 AI/CLI 层无感知切换不同 App：

```python
class BaseDriver(ABC):
    def connect(self) -> "BaseDriver": ...
    def disconnect(self): ...
    def search(self, keyword: str) -> list: ...
```

### `driver/models.py` — 业务数据模型

结构化的 Python dataclass，不依赖任何 UI 解析：

```python
@dataclass
class Restaurant:
    id: str
    name: str
    rating: float
    delivery_time: int    # 分钟
    delivery_fee: float   # 元
    min_order: float      # 起送金额

@dataclass
class MenuItem:
    id: str
    name: str
    price: float

@dataclass
class Order:
    id: str
    status: OrderStatus
    items: list[CartItem]
    total: float
```

### `hooks/meituan_hook.js` — Frida Hook 脚本

拦截美团 App 内部的网络请求和关键函数，提取原始 API 响应：

```javascript
// 拦截 OkHttp 请求
Java.use('okhttp3.OkHttpClient').newCall.implementation = function(request) { ... }

// 拦截加密签名
Java.use('com.meituan.android.common.mtguard.MTGuard').getToken.implementation = function() { ... }
```

### `hooks/ssl_bypass.js` — SSL Pinning 绕过

绕过 App 的 SSL 证书锁定，配合抓包工具使用。

### `gadget_inject.py` — APK 注入工具

将 Frida Gadget 注入到目标 APK，实现无 Root 注入：

```bash
python gadget_inject.py --apk com.sankuai.meituan --output meituan_patched.apk
```

### `sign_server/` — APK 重签名服务

注入后重签名 APK 的 Java 服务：

```bash
bash sign_server/sign_runner.sh meituan_patched.apk
```

---

## 使用方法（WIP）

### 环境准备

```bash
pip install frida-tools typer rich
npm install -g frida-compile   # 编译 frida agent

# 手机需要：
# - Root 权限（或用 Gadget 注入方案）
# - 安装 frida-server
adb push frida-server /data/local/tmp/
adb shell chmod +x /data/local/tmp/frida-server
adb shell /data/local/tmp/frida-server &
```

### Hook 美团（需 Root）

```bash
# 注入 Hook 脚本
frida -U -n com.sankuai.meituan -l hooks/meituan_hook.js

# 或用 CLI
python cli/main.py search "麻辣烫"
```

### 无 Root 方案（Gadget 注入）

```bash
# 1. 注入 Gadget 到 APK
python gadget_inject.py

# 2. 安装注入后的 APK
adb install meituan_patched.apk

# 3. 启动 App 后连接
frida -U -n com.sankuai.meituan -l hooks/meituan_hook.js
```

---

## 扩展到其他 App

参照美团实现，为新 App 创建 Driver：

```python
# driver/douyin_driver.py
from driver.base_driver import BaseDriver

class DouyinDriver(BaseDriver):
    def connect(self): ...
    def search(self, keyword): ...
```

并在 `hooks/` 下添加对应的 JS Hook 脚本。

---

## 文件结构

```
android-app-hook/
├── driver/
│   ├── base_driver.py      # 抽象基类（App 无关）
│   ├── meituan_driver.py   # 美团实现
│   └── models.py           # 业务数据模型
├── cli/
│   └── main.py             # typer CLI（rich 渲染）
├── hooks/
│   ├── meituan_hook.js     # 美团 Frida Hook
│   └── ssl_bypass.js       # SSL Pinning 绕过
├── frida_agent/            # Frida Agent 编译工程
│   └── package.json
├── sign_server/
│   ├── SignServer.java      # APK 重签名服务
│   └── sign_runner.sh
├── gadget_inject.py        # APK Gadget 注入工具
├── meituan_hook.py         # Python 侧 Hook 管理
├── schema/                 # API Schema 定义（待完善）
├── 项目概要.md              # 原始架构设计文档
└── README.md
```

---

## 相关项目

- **[meituan-cli](https://github.com/你的用户名/meituan-cli)** — 基于 UIAutomator2 的稳定实现，已可生产使用

## License

MIT
