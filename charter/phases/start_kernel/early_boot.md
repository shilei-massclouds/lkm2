# EarlyBoot 章程

EarlyBoot 的外层边界从 `start_kernel` 入口开始，到当前 CPU 成功打开全局 S-mode
interrupt 为止。它保留在 `StartKernel → EarlyBoot → BootSetup → BootHandoff` 层级中，
不物化 `objects.early_boot`，也不在本阶段新增地址空间、内存、trap、IRQ 或 timekeeping
生命周期对象。

## 入口与交接

EarlyBoot 入口必须复查 `CurrentTaskRef == BootTask`、BootTask 仍为 OnCpu、
`Cpu0Scheduler` 仍为 Ready，以及 ArchHead 交接的 interrupt-masked 事实。随后它先驱动
`DtbBlob.Enable` 从 `/chosen/bootargs` 建立 `BootCommandLine` 内容，再依次驱动
`EarlyConsole.Enable` 和 `Cpu0Scheduler.Enable`，最后以
`CurrentCPU.InterruptControlRef.Unmask` 作为严格的最后一个 drive。后续任何 EarlyBoot
初始化 drive 都必须插在 Unmask 之前，并单独评审其 ownership 与生命周期。

只有 Unmask 成功后才能建立 `early_boot_interrupts_enabled()`。交接时 BootTask 必须仍为
current 且 OnCpu，DtbBlob、EarlyConsole 和 `Cpu0Scheduler` 必须为 Online，且
`BootCommandLine` 必须包含 `earlycon=sbi`；EarlyBoot 返回后仍由 BootTask 执行，第一次
上下文切换继续归 BootHandoff。DtbBlob binding 或后续任一 drive 失败均不得执行剩余
drive 或建立交接事实；路径保持 interrupt 关闭并 fail-stop，BootSetup 不得越过缺失的
交接事实继续执行。

DtbBlob Online 表示内核已经观察并复制其 bootargs，不表示 DTB 内存失效。本里程碑直接
使用该 lifecycle transition，不物化 `SetupArch`、`parse_dtb` 或 DT 属性包装阶段。

全局 S-mode interrupt enable 只打开 CPU 的全局投递边界，不等于所有设备 interrupt source
均已配置或启用。runtime trap dispatch、IRQ controller、timer 与 timekeeping 的实现就绪
条件仍待后续阶段逐项建模。

## 参考边界

Linux 只提供粗粒度参考锚点：architecture/memory → trap/core memory → scheduler →
IRQ/timer/timekeeping → `local_irq_enable`。这不是本模型已经冻结或 derive 已证明的逐调用
实现顺序；本轮只冻结 Scheduler Online 必须先于最终 Unmask 的外层边界。

临时模型映射：
[`model/phases/start_kernel/early_boot.spec`](../../../model/phases/start_kernel/early_boot.spec)。
