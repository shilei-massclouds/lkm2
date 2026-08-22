# UserAppRuntime 信号程序

本目录保存可复用、受版本控制的 UserAppRuntime 信号输入。信号文件采用逐行文本格式，
可通过以下任一入口使用：

```sh
tools/bin/derive --user-runtime-signals tools/signals/default.signals
make run USER_RUNTIME_SIGNALS=tools/signals/default.signals
```

- `default.signals`：显式表示内存默认程序，向本地 CPU 发送 `syscall.exit(0)`。
- `parked.signals`：只有注释，没有有效信号；使 UserAppRuntime 保持 parked，推导结果为
  `yielded`。

未指定信号文件时，推导器仍使用内存默认程序，不会隐式读取本目录中的文件。
