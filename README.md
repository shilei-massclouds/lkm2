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
make run
make clean
```

当前这些目标委托给 `tools/Makefile`；后续增加其他组件时，根 Makefile 将继续负责组合各组件目标。组件特有的安装、运行和测试方式不在根 README 重复说明。

当前默认推导会完成 BootCPU/CPU0 Scheduler 初始化，把 `KernelInitTask` 放入隐藏
runq，并执行首次 Suspend、事务式 current 切换与 Resume。TaskFlow 保存 CPU
`cpu_ref`，`CurrentCPU` 通过当前 TaskFlow 解引用。`UserRunPhase` 初始化推导器 owned
`UserAppRuntime` 后，默认内存信号 `syscall.exit <local> 0` 先送到 BootCPU；CPU 创建
fresh `SyscallExitFlow0`，再驱动 `KernelInitTask.Action::Exit(0)`。EventFlow 不调用
Scheduler，也不改变 Task 的 `OnCpu` 状态或 runq。Linux PID 1 保护最终以
`Attempted to kill init!` panic，`tools/bin/derive` 返回 1。指定空运行时信号文件可使
推导以 `yielded` 停在用户态并返回 0。仓库内可复用的信号程序保存在
[`tools/signals/`](tools/signals/)；详细语义与因果输出见工具说明。
