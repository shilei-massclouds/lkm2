# EarlyBoot 章程

EarlyBoot 覆盖 `start_kernel` 入口到早期虚拟内存完成。完整外层顺序为
`StartKernel → EarlyBoot → BootSetup → BootHandoff`；EarlyBoot 只有一个 `Enter` 动作，按固定
顺序展开 DTB 输入、Memory/Reserved/MemBlock、最终页表、Scheduler 和中断交接。它负责 DTB 的
启动输入、`parse_dtb` 对应的 Memory 建立、保留区提交和最终页表切换。

## 入口与交接

EarlyBoot 入口必须复查 `CurrentTaskRef == BootTask`、BootTask 仍为 OnCpu，以及 ArchHead
交接的 interrupt-masked 事实。随后依次执行：

1. `Banner.Enable` 通过已经 Online 的 Printk 提交 banner；Console 此时可以尚未 Online；
2. `DtbBlob.Enable` 从 `/chosen/bootargs` 建立 `BootCommandLine`；
3. `MemBlockMemory.Enable` 从 `/memory` 建立系统 RAM；
4. `SbiCapability.Enable` 提交 SBI 探测结果；
5. `EarlyConsole.Enable` 完成 registry binding，并嵌套驱动 `SbiConsole.Enable`。

因此固定直接 drive 顺序为
`Banner → DtbBlob → MemBlockMemory → SbiCapability → EarlyConsole → MemBlockReserved → MemBlock →
SwapperPageTable → Cpu0Scheduler → Unmask`。

交接时 BootTask 必须仍为 current 且 OnCpu，Banner、DtbBlob、MemBlockMemory、
`SbiCapability`、EarlyConsole、MemBlockReserved、MemBlock、SwapperPageTable 与 Cpu0Scheduler
必须为 Online，并保留
`early_console_bound_from_registry(EarlyConsole, SbiConsole)`、
`printk_console_registered(Printk, EarlyConsole)` 以及 `BootCommandLine` 的 `earlycon` 键。成功
完成全部 drive 后建立 `early_boot_interrupts_enabled()`，供 BootSetup 消费；EarlyBoot 不断言该
键的具体外部输入值。

若 DTB 物理范围、bootargs、Memory 输入或后续任一 drive 失败，已经提交的前序对象保持其状态，
但不得执行剩余 drive；BootSetup 不得执行。若 DTB 缺少非空且有效的 memory
描述，三个 MemBlock 对象都保持 Ready，且不得执行 SBI capability 或 Console。

若 `SbiCapability` 探测完成但没有 transport availability，失败发生在后续
`SbiConsole.Enable` 的 depends_on；`SbiCapability` 保持 Online，后续动作与 BootSetup 均
不得执行。

## 范围边界

DtbBlob Online 表示内核已经观察并复制 bootargs，不表示 DTB 内存失效，也不表示 DTB
`/memory` 描述等同于 DTB 文件自身范围。MemBlockMemory 从 `/memory` 建立系统 RAM 来源；
MemBlockReserved 则由 EarlyBoot 的 `Enter` 后半段表示 KernelImage、DTB 文件自身、FDT
reserve map 和 `/reserved-memory` 要求的强制保留完整。本模型不额外物化 `SetupArch`、
`parse_dtb` 或 DT 属性包装阶段。

对象输入所有权见 [`DtbBlob` 章程](../../objects/dtb_blob.md) 与
[`MemBlock` 章程](../../objects/memblock.md)。模型在 `SwapperPageTable` 后继续展开 Scheduler
与 interrupt Unmask；本里程碑的 Rust 入口仍在 M1 成功后停驻且中断保持屏蔽。

模型映射：
[`model/phases/start_kernel/early_boot.spec`](../../../model/phases/start_kernel/early_boot.spec)。
