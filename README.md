# LKM

本仓库维护 LKM 的模型规格、设计文档和配套工具。仓库根目录只负责组织各组件及提供统一协调入口；各组件的环境、构建方式和使用说明由组件目录自行维护。

## 目录

- `model/`：模型入口和系统规格。
- `tools/`：模型工具链，详见 [`tools/README.md`](tools/README.md)。
- `docs/`：总体计划和设计文档。
- `Makefile`：将统一目标委托给各组件 Makefile。

## 统一入口

在仓库根目录可以使用以下协调目标：

```sh
make setup
make build
make test
make derive
make run  # 暂时保留的空操作
make clean
```

当前 `setup`、`build`、`test`、`derive` 和 `clean` 由根 Makefile 委托给
`tools/Makefile`，其中根目录 `make derive` 对应组件入口 `make -C tools run`。
根目录 `make run` 暂时保留为空操作：不构建、不推导、无输出并成功退出。后续增加
其他组件时，根 Makefile 将继续负责组合各组件目标。组件特有的安装、运行和测试方式
不在根 README 重复说明。

根目录 `make derive` 与组件入口 `make -C tools run` 默认显式使用
`tools/signals/parked.signals`。推导会完成 BootCPU/CPU0 Scheduler 初始化、首次
Suspend、事务式 current 切换与 Resume，然后停在 UserAppRuntime，以 `yielded` 返回
0。直接无参数调用 `tools/bin/derive` 的行为不变：它使用内存默认信号
`syscall.exit <local> 0`，最终触发 PID 1 的 `Attempted to kill init!` panic 并返回
1。`make derive USER_RUNTIME_SIGNALS=tools/signals/default.signals` 可显式选择相同行为；
`make derive USER_RUNTIME_SIGNALS=` 会省略信号文件参数，也回退到推导器的内存默认
程序。
仓库内可复用的信号程序保存在 [`tools/signals/`](tools/signals/)；详细语义与因果输出
见工具说明。
