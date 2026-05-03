#!/system/bin/sh
# 在 Android 上用 dalvikvm 运行，调用美团的签名 DEX
# 用法: sh sign_runner.sh "param1=v1&param2=v2"

PARAMS="$1"
MEITUAN_DEX="/data/app/~~NO_jGzFgpowdKqhb7dIdMA==/com.sankuai.meituan-ms4H0LGZrcx7tE8XRJ4E9g==/base.apk"
GUARD_DEX="/data/app/~~NO_jGzFgpowdKqhb7dIdMA==/com.sankuai.meituan-ms4H0LGZrcx7tE8XRJ4E9g==/lib/arm/libmtguard_log.so"

# 用 app_process 加载并调用 ShellBridge.main3
CLASSPATH="$MEITUAN_DEX:$GUARD_DEX" \
  app_process \
  /system/bin \
  com.meituan.android.common.mtguard.SignCaller \
  "$PARAMS"
