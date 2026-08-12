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

当前默认推导会完成 CPU0 Scheduler 初始化，把 `KernelInitTask` 放入推导器维护的
隐藏 runq，并完整执行 Suspend、事务式 current 切换、Resume、Dequeue 与 TaskFlow 恢复。
最终 CPU0 current Task 为 `KernelInitTask`、runq 为空，BootHandoff 保留可恢复的
yielded continuation；`tools/bin/derive`/`make run` 因而正常返回 0。详细语义与
因果输出见工具说明。
