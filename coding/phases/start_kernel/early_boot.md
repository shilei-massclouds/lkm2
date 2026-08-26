# EarlyBoot 编码约束

本页对应
[`model/phases/start_kernel/early_boot.spec`](../../../model/phases/start_kernel/early_boot.spec)。
当前 [`impl/phases/start_kernel.rs`](../../../impl/phases/start_kernel.rs) 保持不变；真实 runtime
trap、IRQ/timekeeping 初始化和硬件 Unmask 留给后续实现阶段。

## 固定控制边界

`EarlyBoot.Action::Enter` 覆盖从 `start_kernel` 到全局 S-mode interrupt enable 成功的范围：

1. 复查 BootTask 是 current 且 OnCpu、`Cpu0Scheduler.state == Ready`，并确认 ArchHead
   交接时 interrupts masked；
2. 驱动 `DtbBlob.Transition::Enable`，从其 `/chosen/bootargs` 子关系把
   `earlycon=sbi` 复制到静态存在、初始为空的 `BootCommandLine`；
3. 驱动 `EarlyConsole.Transition::Enable`，依次查询命令行和静态 earlycon 注册表；
4. 驱动 `Cpu0Scheduler.Transition::Enable`，使 CPU0 Scheduler 进入 Online；
5. 最后驱动 `CurrentCPU.InterruptControlRef.Action::Unmask`；
6. 仅在 DtbBlob、EarlyConsole、Scheduler 均为 Online，且命令行包含
   `earlycon=sbi` 后建立 `early_boot_interrupts_enabled()`。

Unmask 必须始终是 EarlyBoot 的最后一个 drive；任何后续引入的 architecture、memory、
trap、IRQ、timer 或 timekeeping 初始化都只能插入它之前，并需要独立冻结 ownership、状态
与失败语义。Unmask 失败时不得发布成功事实或进入 BootSetup，且必须维持 interrupts
关闭的 fail-stop 路径。

M2 直接用 `DtbBlob.Enable` 表达“内核观察并复制 bootargs”的生命周期边界，不增加
`SetupArch` 或 `parse_dtb` 具名阶段，也不把字符串扫描算法纳入 model。DtbBlob binding
失败时，后续 EarlyConsole、Scheduler 和 Unmask drive 均不得发生；Online 不表示 DTB
内存失效。

BootSetup 入口复查 `early_boot_interrupts_enabled()`、BootTask current/OnCpu 和 Scheduler
Online，之后只驱动 `KernelInitTask` 的 Preset、Setup、Enable。BootHandoff 仍拥有首次
Scheduler Schedule 和第一次上下文切换。

全局 interrupt enable 不代表设备 interrupt source 已启用，也不把 ArchHead 安装的
StartKernel-safe early fail-stop trap 提升为 runtime dispatch。Linux 的
architecture/memory → trap/core memory → scheduler → IRQ/timer/timekeeping →
`local_irq_enable` 仅作粗粒度审阅锚点，不是本轮模型冻结的逐调用顺序。
