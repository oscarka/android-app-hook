/**
 * SSL Pinning 绕过脚本
 *
 * 美团使用了 HTTPS 证书绑定（Certificate Pinning），防止中间人抓包。
 * 我们需要绕过它，让 Frida 能读取明文 HTTP 响应体。
 *
 * 覆盖方案：
 *   1. 绕过 Android 系统级别的 SSL 验证 (TrustManager)
 *   2. 绕过 OkHttp 的 CertificatePinner
 *   3. 绕过 Conscrypt (Google 的 TLS 实现)
 */

Java.perform(function () {

    // ── 方案1: 替换全局 TrustManager（最通用）──
    try {
        var X509TrustManager = Java.use("javax.net.ssl.X509TrustManager");
        var SSLContext = Java.use("javax.net.ssl.SSLContext");

        // 创建一个什么都信任的 TrustManager
        var TrustManager = Java.registerClass({
            name: "com.hook.TrustAllManager",
            implements: [X509TrustManager],
            methods: {
                checkClientTrusted: function (chain, authType) {},
                checkServerTrusted: function (chain, authType) {},
                getAcceptedIssuers: function () { return []; }
            }
        });

        var TrustManagers = [TrustManager.$new()];
        var SSLContextInstance = SSLContext.getInstance("TLS");
        SSLContextInstance.init(null, TrustManagers, null);

        // 设置为默认 SSLContext
        var SSLSocketFactory = Java.use("javax.net.ssl.HttpsURLConnection");
        SSLSocketFactory.setDefaultSSLSocketFactory(
            SSLContextInstance.getSocketFactory()
        );

        console.log("[SSL Bypass] TrustManager 已替换 ✓");
    } catch (e) {
        console.log("[SSL Bypass] TrustManager 替换失败: " + e);
    }

    // ── 方案2: 绕过 OkHttp CertificatePinner ──
    try {
        var CertificatePinner = Java.use("okhttp3.CertificatePinner");

        CertificatePinner.check.overload(
            "java.lang.String",
            "java.util.List"
        ).implementation = function (hostname, peerCertificates) {
            console.log("[SSL Bypass] 跳过 OkHttp3 CertificatePinner: " + hostname);
            return;
        };

        // 旧版 OkHttp API
        CertificatePinner.check.overload(
            "java.lang.String",
            "[Ljava.security.cert.Certificate;"
        ).implementation = function (hostname, peerCertificates) {
            return;
        };

        console.log("[SSL Bypass] OkHttp CertificatePinner 已绕过 ✓");
    } catch (e) {
        console.log("[SSL Bypass] OkHttp Pinner 不存在或绕过失败: " + e);
    }

    // ── 方案3: 绕过 HostnameVerifier ──
    try {
        var HostnameVerifier = Java.use("javax.net.ssl.HostnameVerifier");
        var HttpsURLConnection = Java.use("javax.net.ssl.HttpsURLConnection");

        var TrustAllHostnameVerifier = Java.registerClass({
            name: "com.hook.TrustAllHostnames",
            implements: [HostnameVerifier],
            methods: {
                verify: function (hostname, session) { return true; }
            }
        });

        HttpsURLConnection.setDefaultHostnameVerifier(TrustAllHostnameVerifier.$new());
        console.log("[SSL Bypass] HostnameVerifier 已替换 ✓");
    } catch (e) {
        console.log("[SSL Bypass] HostnameVerifier 替换失败: " + e);
    }

    // ── 方案4: 绕过 网络安全配置 (Android 7+) ──
    try {
        var NetworkSecurityTrustManager = Java.use(
            "android.security.net.config.NetworkSecurityTrustManager"
        );
        NetworkSecurityTrustManager.checkPins.implementation = function (chain) {
            console.log("[SSL Bypass] 跳过 NetworkSecurityConfig Pin 检查 ✓");
        };
    } catch (e) {
        // Android 版本不同可能没这个类
    }

    console.log("[SSL Bypass] 初始化完成");
});
