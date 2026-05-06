/**
 * 美团外卖 Frida Agent v3.0 (Frida 17 兼容版)
 *
 * Frida 17 变更：Java bridge 不再内置，需要从 frida-java-bridge 导入
 * 编译命令: frida-compile agent.ts -o _agent.js -c
 */

import Java from "frida-java-bridge";

// ══════════════════════════════════════════════
//  反反调试：修复美团的 SIGSEGV 信号陷阱
//  美团在 libmgc.so JNI_OnLoad 里故意访问非法地址（0xeb47ab44）
//  通过把 PC 设置为 LR（返回地址），让陷阱代码直接返回，不再循环
// ══════════════════════════════════════════════

Process.setExceptionHandler(details => {
    if (details.type === "access-violation") {
        // 把 PC 设为 LR（让当前函数直接返回，不再循环）
        const ctx = details.context as any;
        try {
            // ARM32: lr 寄存器
            if (ctx.lr) {
                ctx.pc = ctx.lr;
                return true;
            }
        } catch {}
        try {
            // ARM64: x30 是 LR
            if (ctx.x30) {
                ctx.pc = ctx.x30;
                return true;
            }
        } catch {}
        return true;  // 兜底
    }
    return false;
});

console.log("[AntiDebug] 异常处理器已安装（PC 修复模式）✓");

// ══════════════════════════════════════════════
//  RPC 层（顶层，立即注册）
// ══════════════════════════════════════════════

rpc.exports = {
    ping(): object {
        return {
            ok: true,
            timestamp: Date.now(),
            tag: "meituan_hook",
            version: "3.0",
            java_available: Java.available
        };
    },

    async triggerSearch(keyword: string): Promise<object> {
        return new Promise(resolve => {
            Java.perform(() => {
                try {
                    const Intent = Java.use("android.content.Intent");
                    const Uri = Java.use("android.net.Uri");
                    const AT = Java.use("android.app.ActivityThread");
                    const ctx = AT.currentApplication().getApplicationContext();
                    const uri = Uri.parse("meituan://waimai/search?keyword=" + encodeURIComponent(keyword));
                    const intent = Intent.$new("android.intent.action.VIEW", uri);
                    intent.setFlags(0x10000000);
                    ctx.startActivity(intent);
                    resolve({ ok: true, method: "deeplink" });
                } catch (e: any) {
                    resolve({ ok: false, error: e.toString() });
                }
            });
        });
    }
};

// ══════════════════════════════════════════════
//  Java Hook 初始化
// ══════════════════════════════════════════════

console.log("[Meituan Hook v3.0] Java.available =", Java.available);

if (Java.available) {
    // SSL bypass
    Java.perform(() => {
        _bypassAntiDebug();
        _bypassSSL();
        _hookOkHttp();
    });
} else {
    console.log("[Hook] Java not available, waiting...");
    // 等待 Java 就绪
    let attempts = 0;
    const timer = setInterval(() => {
        attempts++;
        if (Java.available) {
            clearInterval(timer);
            console.log("[Hook] Java is now available after", attempts, "attempts");
            Java.perform(() => {
                _bypassSSL();
                _hookOkHttp();
            });
        } else if (attempts > 100) {
            clearInterval(timer);
            console.log("[Hook] Java never became available after", attempts, "attempts");
        }
    }, 100);
}

// ══════════════════════════════════════════════
//  反反调试：Hook 美团 mtguard 检测层
// ══════════════════════════════════════════════

function _bypassAntiDebug(): void {
    // 1. Hook ShellBridge.main — 美团反调试的核心 JNI 接口
    //    cmd 指令中某些值会触发 SIGSEGV 陷阱（如访问 0xeb47ab44）
    try {
        const SB = Java.use("com.meituan.android.common.mtguard.ShellBridge");
        SB.main.implementation = function(cmd: number, args: any) {
            // cmd=1 是初始化，放过；其他反调试指令直接返回 null
            if (cmd === 1 || cmd === 2 || cmd === 3) {
                return (this as any).main(cmd, args);
            }
            console.log("[AntiDebug] ShellBridge.main blocked cmd=" + cmd);
            return null;
        };
        console.log("[AntiDebug] ShellBridge.main ✓");
    } catch (e) {
        console.log("[AntiDebug] ShellBridge (not found, may be ok):", e);
    }

    // 2. Hook CoreUtils — 另一个检测 frida 的工具类
    try {
        const CU = Java.use("com.meituan.android.common.unionid.oneid.util.CoreUtils");
        if ((CU as any).statFile) {
            (CU as any).statFile.implementation = function(path: string) {
                // 阻止检测 /proc/net/tcp 等 frida 特征文件
                if (path && (path.includes("frida") || path.includes("27042"))) {
                    console.log("[AntiDebug] CoreUtils.statFile blocked:", path);
                    return "0";
                }
                return (this as any).statFile(path);
            };
        }
        console.log("[AntiDebug] CoreUtils ✓");
    } catch (e) { /* 不一定存在 */ }
}

// ══════════════════════════════════════════════
//  SSL Bypass
// ══════════════════════════════════════════════

function _bypassSSL(): void {
    try {
        const X509 = Java.use("javax.net.ssl.X509TrustManager");
        const SSLCtx = Java.use("javax.net.ssl.SSLContext");
        const TM = Java.registerClass({
            name: "com.hook.TrustAll_" + Date.now(),
            implements: [X509],
            methods: {
                checkClientTrusted() {},
                checkServerTrusted() {},
                getAcceptedIssuers() { return []; }
            }
        });
        const ctx = SSLCtx.getInstance("TLS");
        ctx.init(null, [TM.$new()], null);
        Java.use("javax.net.ssl.HttpsURLConnection").setDefaultSSLSocketFactory(ctx.getSocketFactory());
        console.log("[SSL] TrustManager ✓");
    } catch (e) { console.log("[SSL] TrustManager error:", e); }

    try {
        const CP = Java.use("okhttp3.CertificatePinner");
        CP.check.overload("java.lang.String", "java.util.List").implementation = function(h: any) {
            console.log("[SSL] Bypassing CertPinner:", h);
        };
        CP.check.overload("java.lang.String", "[Ljava.security.cert.Certificate;").implementation = function() {};
        console.log("[SSL] OkHttp CertPinner ✓");
    } catch (e) { console.log("[SSL] CertPinner:", e); }
}

// ══════════════════════════════════════════════
//  OkHttp Hook
// ══════════════════════════════════════════════

function _isMeituanApi(url: string): boolean {
    // 扩大捕获范围，美团所有 API
    const patterns = [
        "api.meituan", "waimai.meituan", "food.meituan", "wm.meituan",
        "meituan.com", "meituan.net", "sankuai.com",
        "mobile.meituan", "i.meituan", "s.meituan"
    ];
    return patterns.some(p => url.includes(p));
}

function _tryJson(s: string): any {
    try { return JSON.parse(s); } catch { return s; }
}

function _sendApi(url: string, method: string, body: any): void {
    send({ type: "response", url, method, body, timestamp: Date.now() });
}

function _hookOkHttp(): void {
    // 同步请求
    try {
        const RealCall = Java.use("okhttp3.RealCall");
        RealCall.execute.implementation = function(this: any) {
            const resp = this.execute();
            const url = this.request().url().toString();
            if (_isMeituanApi(url)) {
                try { _sendApi(url, this.request().method(), _tryJson(resp.peekBody(2 * 1024 * 1024).string())); } catch {}
            }
            return resp;
        };
        console.log("[OkHttp] RealCall.execute ✓");
    } catch (e) { console.log("[OkHttp] execute failed:", e); }

    // 异步请求：hook 内部 AsyncCall.execute，在工作线程里跑，不阻塞 UI
    try {
        const AsyncCall = Java.use("okhttp3.RealCall$AsyncCall");
        AsyncCall.execute.implementation = function(this: any) {
            // 先调用原始逻辑（让网络请求正常完成）
            this.execute();
            // 然后从 response 读取结果（此时已在工作线程，安全）
            // AsyncCall 持有 responseCallback，通过 call() 取得 Request
            try {
                const call = (this as any).call ? (this as any).call() :
                             (this as any).get ? (this as any).get() : null;
                if (call) {
                    const url = call.request().url().toString();
                    if (_isMeituanApi(url)) {
                        send({ type: "request", url, method: call.request().method(), timestamp: Date.now() });
                    }
                }
            } catch {}
        };
        console.log("[OkHttp] AsyncCall.execute ✓");
    } catch (e) { console.log("[OkHttp] AsyncCall failed:", e); }

    // 最终兜底：hook Response.body() 读取，捕获所有响应
    try {
        const ResponseBody = Java.use("okhttp3.ResponseBody");
        ResponseBody.string.implementation = function(this: any) {
            const body = this.string();
            // 无法直接拿到 URL，但可以通过调用栈判断
            // 这里只 send 包含美团特征的响应体
            if (body && body.length > 10 && body.length < 500000) {
                try {
                    const parsed = JSON.parse(body);
                    if (parsed && (parsed.code !== undefined || parsed.data !== undefined ||
                        parsed.msg !== undefined || parsed.errno !== undefined)) {
                        send({ type: "body", body: parsed, timestamp: Date.now() });
                    }
                } catch {}
            }
            return body;
        };
        console.log("[OkHttp] ResponseBody.string ✓");
    } catch (e) { console.log("[OkHttp] ResponseBody failed:", e); }

    console.log("[Meituan Hook v3.0] 初始化完成 ✓");
}

