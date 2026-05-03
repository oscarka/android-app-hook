import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpExchange;
import java.io.*;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.lang.reflect.Method;

/**
 * 在 Android 设备上运行的签名服务（通过 app_process 启动）
 * 调用美团的 ShellBridge.main3 生成 waimai_sign
 */
public class SignServer {
    public static void main(String[] args) throws Exception {
        System.out.println("[SignServer] Starting on port 18080...");
        
        HttpServer server = HttpServer.create(new InetSocketAddress(18080), 0);
        server.createContext("/sign", new SignHandler());
        server.createContext("/ping", exchange -> {
            byte[] resp = "pong".getBytes();
            exchange.sendResponseHeaders(200, resp.length);
            exchange.getResponseBody().write(resp);
            exchange.getResponseBody().close();
        });
        server.start();
        System.out.println("[SignServer] Ready!");
    }
    
    static class SignHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            String query = exchange.getRequestURI().getQuery();
            String params = query != null ? URLDecoder.decode(query.replace("params=", ""), "UTF-8") : "";
            
            String sign = "";
            try {
                // 通过反射调用 ShellBridge.main3
                Class<?> cls = Class.forName("com.meituan.android.common.mtguard.ShellBridge");
                Method m = cls.getMethod("main3", String.class, String[].class);
                String[] input = {params, ""};
                String[] result = (String[]) m.invoke(null, "sign", input);
                sign = result != null && result[0] != null ? result[0] : "";
            } catch (Exception e) {
                sign = "ERROR:" + e.getMessage();
            }
            
            byte[] resp = sign.getBytes("UTF-8");
            exchange.sendResponseHeaders(200, resp.length);
            exchange.getResponseBody().write(resp);
            exchange.getResponseBody().close();
        }
    }
}
