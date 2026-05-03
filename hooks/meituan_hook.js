/**
 * 美团外卖 Hook 核心脚本 v2.0
 *
 * 架构说明：
 *   - rpc.exports 在顶层注册（不依赖 Java），script.load() 后立即可用
 *   - Java.perform 内的 Hook 通过 waitForJava 在运行时就绪后延迟执行
 *   - 兼容 spawn 模式（冷启动注入）和 attach 模式
 *
 * 数据格式（send 回流）：
 *   { type: "response", url: "...", body: {...} }
 *   { type: "request",  url: "...", body: {...} }
 */

// ══════════════════════════════════════════════
//  RPC 层（顶层，立即注册，无需 Java 就绪）
// ══════════════════════════════════════════════

rpc.exports = {

    /**
     * 健康检查 — 无需 Java，script.load() 后立即可用
     */
    ping: function () {
        return { ok: true, timestamp: Date.now(), tag: "meituan_hook", version: "2.0" };
    },

    /**
     * 触发美团搜索（需要 Java，异步）
     */
    triggerSearch: function (keyword) {
        return new Promise(function (resolve) {
            Java.perform(function () {
                try {
                    var Intent = Java.use("android.content.Intent");
                    var Uri    = Java.use("android.net.Uri");
                    var AT     = Java.use("android.app.ActivityThread");
                    var ctx    = AT.currentApplication().getApplicationContext();

                    try {
                        var uri    = Uri.parse("meituan://waimai/search?keyword=" + encodeURIComponent(keyword));
                        var intent = Intent.$new("android.intent.action.VIEW", uri);
                        intent.setFlags(0x10000000);
                        ctx.startActivity(intent);
                        resolve({ ok: true, method: "deeplink" });
                    } catch (e1) {
                        var i2 = Intent.$new();
                        i2.setAction("android.intent.action.SEARCH");
                        i2.putExtra("query", keyword);
                        i2.setFlags(0x10000000);
                        ctx.startActivity(i2);
                        resolve({ ok: true, method: "search_intent" });
                    }
                } catch (e) {
                    resolve({ ok: false, error: e.toString() });
                }
            });
        });
    },

    /**
     * 购物车加购（RPC 存根，待 sniff 后实现）
     */
    addToCart: function (itemId, quantity) {
        return { ok: false, message: "addToCart RPC 待实现，请先用 sniff 找到 CartManager 类名" };
    },

    /**
     * 获取当前屏幕 View 层级（调试用）
     */
    getViewHierarchy: function () {
        return new Promise(function (resolve) {
            Java.perform(function () {
                try {
                    var WMG   = Java.use("android.view.WindowManagerGlobal");
                    var roots = WMG.getInstance().mRoots.value;
                    if (roots && roots.size() > 0) {
                        var view = roots.get(roots.size() - 1).mView.value;
                        resolve(_dumpView(view, 0, 3));
                    } else {
                        resolve("No root view");
                    }
                } catch (e) {
                    resolve("Error: " + e);
                }
            });
        });
    }
};


// ══════════════════════════════════════════════
//  Hook 层（等待 Java 运行时就绪后执行）
// ══════════════════════════════════════════════

(function hookWhenReady() {
    if (typeof Java === "undefined" || !Java.available) {
        setTimeout(hookWhenReady, 30);
        return;
    }

    // --- SSL Bypass ---
    Java.perform(function () {
        _bypassSSL();
    });

    // --- OkHttp Hook ---
    Java.perform(function () {
        _hookOkHttp();
    });
})();


// ══════════════════════════════════════════════
//  SSL Pinning 绕过
// ══════════════════════════════════════════════

function _bypassSSL() {
    try {
        var X509TrustManager = Java.use("javax.net.ssl.X509TrustManager");
        var SSLContext = Java.use("javax.net.ssl.SSLContext");

        var TrustManager = Java.registerClass({
            name: "com.hook.TrustAll_" + Date.now(),
            implements: [X509TrustManager],
            methods: {
                checkClientTrusted: function () {},
                checkServerTrusted: function () {},
                getAcceptedIssuers: function () { return []; }
            }
        });

        var ctx = SSLContext.getInstance("TLS");
        ctx.init(null, [TrustManager.$new()], null);
        Java.use("javax.net.ssl.HttpsURLConnection").setDefaultSSLSocketFactory(ctx.getSocketFactory());
        console.log("[SSL] TrustManager 替换 ✓");
    } catch (e) { console.log("[SSL] TrustManager: " + e); }

    try {
        var CertPinner = Java.use("okhttp3.CertificatePinner");
        CertPinner.check.overload("java.lang.String", "java.util.List").implementation = function (h, c) {
            console.log("[SSL] 跳过 CertificatePinner: " + h);
        };
        CertPinner.check.overload("java.lang.String", "[Ljava.security.cert.Certificate;").implementation = function () {};
        console.log("[SSL] OkHttp CertificatePinner 绕过 ✓");
    } catch (e) { console.log("[SSL] CertPinner: " + e); }

    try {
        var NSConfig = Java.use("android.security.net.config.NetworkSecurityTrustManager");
        NSConfig.checkPins.implementation = function () {};
        console.log("[SSL] NetworkSecurityConfig 绕过 ✓");
    } catch (e) {}
}


// ══════════════════════════════════════════════
//  OkHttp 网络层 Hook
// ══════════════════════════════════════════════

function _hookOkHttp() {
    var TAG = "[OkHttp]";

    // 同步请求
    try {
        var RealCall = Java.use("okhttp3.RealCall");
        RealCall.execute.implementation = function () {
            var resp    = this.execute();
            var url     = this.request().url().toString();
            if (_isMeituanApi(url)) {
                try {
                    var bodyStr = resp.peekBody(2 * 1024 * 1024).string();
                    send({
                        type:      "response",
                        url:       url,
                        method:    this.request().method(),
                        body:      _tryJson(bodyStr),
                        timestamp: Date.now()
                    });
                } catch (e) {}
            }
            return resp;
        };
        console.log(TAG + " RealCall.execute ✓");
    } catch (e) { console.log(TAG + " execute Hook 失败: " + e); }

    // 异步请求
    try {
        var Callback  = Java.use("okhttp3.Callback");
        var RealCall2 = Java.use("okhttp3.RealCall");

        RealCall2.enqueue.implementation = function (cb) {
            var url = this.request().url().toString();
            if (!_isMeituanApi(url)) { return this.enqueue(cb); }

            var method = this.request().method();
            var Wrapped = Java.registerClass({
                name: "com.hook.CB_" + Date.now(),
                implements: [Callback],
                methods: {
                    onFailure: function (call, e) { cb.onFailure(call, e); },
                    onResponse: function (call, resp) {
                        try {
                            send({
                                type:      "response",
                                url:       url,
                                method:    method,
                                body:      _tryJson(resp.peekBody(2 * 1024 * 1024).string()),
                                timestamp: Date.now()
                            });
                        } catch (ex) {}
                        cb.onResponse(call, resp);
                    }
                }
            });
            return this.enqueue(Wrapped.$new());
        };
        console.log(TAG + " RealCall.enqueue ✓");
    } catch (e) { console.log(TAG + " enqueue Hook 失败: " + e); }

    console.log("[Meituan Hook] 全部 Hook 初始化完成 ✓");
}


// ══════════════════════════════════════════════
//  工具函数
// ══════════════════════════════════════════════

function _isMeituanApi(url) {
    var patterns = [
        "api.meituan.com", "waimai.meituan.com", "food.meituan.com",
        "wm.meituan.com", "meituangroup.com",
        "/waimai/", "/food/", "/poi/", "/order/", "/cart"
    ];
    for (var i = 0; i < patterns.length; i++) {
        if (url.indexOf(patterns[i]) !== -1) return true;
    }
    return false;
}

function _tryJson(str) {
    try { return JSON.parse(str); } catch (e) { return str; }
}

function _dumpView(view, depth, maxDepth) {
    if (depth > maxDepth) return "";
    try {
        var indent = "  ".repeat(depth);
        var cls    = view.getClass().getSimpleName();
        var result = indent + cls + "\n";
        var VG     = Java.use("android.view.ViewGroup");
        var vg     = Java.cast(view, VG);
        for (var i = 0; i < vg.getChildCount(); i++) {
            result += _dumpView(vg.getChildAt(i), depth + 1, maxDepth);
        }
        return result;
    } catch (e) { return ""; }
}
