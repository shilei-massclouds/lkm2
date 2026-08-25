# LKM2

本仓库维护 LKM2 的模型规格、设计文档、Rust 实现和配套工具。仓库根目录负责组织各组件
并提供统一协调入口。

## 目录

- `model/`：模型入口和系统规格。
- `coding/`：从模型到实现的编码约束。
- `impl/`：不依赖 Cargo 和外部 crate 的 Rust 内核实现。
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
make run
make clean
```

根目录 `make build` 依次构建工具链与 Rust 实现，`make clean` 清理这两个组件的构建
产物。`make derive` 仍对应 `make -C tools run`；`make run` 对应
`make -C impl run`，会构建 raw kernel image 并使用 QEMU virt/OpenSBI 启动。入口空壳
不会产生 LKM2 串口输出，并会一直停驻；在 QEMU 终端中按 `Ctrl-a x` 退出。

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

## 源码和参考基线

项目固定采用以下 sibling 布局：

```text
<parent>/
├── lkm2/
└── linux-6.12/
```

`../linux-6.12` 仅作为只读的 Linux 机制参考与显式 checkpoint 差分基线。普通构建不
依赖它，也不会检查它是否存在；只有 `make -C impl checkpoint-sibling-patch` 和后续显式
apply/diff 目标会校验冻结的 sibling。生成 patch 不会修改、暂存或提交 sibling。Rust
实现不使用 Cargo，不从 registry、Git 或 vendor 目录引入外部 crate，只使用仓库源码与
固定工具链提供的 sysroot crate。

当前只支持 RISC-V 64 位；这一架构范围同时适用于 LKM2 和 `../linux-6.12` sibling。
实现的默认 Rust target 是 `riscv64imac-unknown-none-elf`，不支持其他架构。后续实现
内容将以该 sibling 中的 RISC-V 64 位机制、顺序和差分行为为参考。

实现 checkpoint 默认使用可完全优化消除的 `empty` handler。可用
`make -C impl CHECKPOINT_HANDLER=debugcon build` 生成原始 SBI DBCN 记录；
`make -C impl test-checkpoints` 会分别验证 Sv57、Sv48 和 Sv39。checkpoint ABI、28 项冻结
清单以及 sibling 审查门见 [`coding/checkpoints.md`](coding/checkpoints.md)。
