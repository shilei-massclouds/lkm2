# EarlyBoot 章程

EarlyBoot 覆盖 `start_kernel` 入口到 EarlyConsole 成功注册。完整外层顺序为
`StartKernel → EarlyBoot → PagingInit → BootSetup → BootHandoff`。EarlyBoot 负责 DTB 的启动
输入、`parse_dtb` 对应的 Memory 建立以及早期控制台，不拥有 `setup_bootmem`、最终页表、
Scheduler 或 interrupt Unmask。

## 入口与交接

EarlyBoot 入口必须复查 `CurrentTaskRef == BootTask`、BootTask 仍为 OnCpu，以及 ArchHead
交接的 interrupt-masked 事实。随后依次执行：

1. `Banner.Enable` 通过已经 Online 的 Printk 提交 banner；Console 此时可以尚未 Online；
2. `DtbBlob.Enable` 从 `/chosen/bootargs` 建立 `BootCommandLine`；
3. `MemBlockMemory.Enable` 从 `/memory` 建立系统 RAM；
4. `SbiCapability.Enable` 提交 SBI 探测结果；
5. `EarlyConsole.Enable` 完成 registry binding，并嵌套驱动 `SbiConsole.Enable`。

因此固定直接 drive 顺序为
`Banner → DtbBlob → MemBlockMemory → SbiCapability → EarlyConsole`。

交接时 BootTask 必须仍为 current 且 OnCpu，Banner、DtbBlob、MemBlockMemory、
`SbiCapability` 与 EarlyConsole 必须为 Online，并保留
`early_console_bound_from_registry(EarlyConsole, SbiConsole)`、
`printk_console_registered(Printk, EarlyConsole)` 以及 `BootCommandLine` 的 `earlycon` 键。
EarlyBoot 不断言该键的具体外部输入值，也不建立 interrupt-enabled 交接事实。

若 DTB 物理范围、bootargs、Memory 输入或后续任一 drive 失败，已经提交的前序对象保持其状态，
但不得执行剩余 drive；PagingInit 与 BootSetup 均不得执行。若 DTB 缺少非空且有效的 memory
描述，三个 MemBlock 对象都保持 Ready，且不得执行 SBI capability 或 Console。

若 `SbiCapability` 探测完成但没有 transport availability，失败发生在后续
`SbiConsole.Enable` 的 depends_on；`SbiCapability` 保持 Online，PagingInit 与 BootSetup 均
不得执行。

## 范围边界

DtbBlob Online 表示内核已经观察并复制 bootargs，不表示 DTB 内存失效，也不表示 DTB
`/memory` 描述等同于 DTB 文件自身范围。MemBlockMemory 从 `/memory` 建立系统 RAM 来源；
MemBlockReserved 则由 PagingInit 中的 `setup_bootmem` 表示 KernelImage、DTB 文件自身、FDT
reserve map 和 `/reserved-memory` 要求的强制保留完整。本模型不额外物化 `SetupArch`、
`parse_dtb` 或 DT 属性包装阶段。

对象输入所有权见 [`DtbBlob` 章程](../../objects/dtb_blob.md) 与
[`MemBlock` 章程](../../objects/memblock.md)。后续 reservation、页表、Scheduler 与 interrupt
交接见 [`PagingInit` 章程](paging_init.md)。

模型映射：
[`model/phases/start_kernel/early_boot.spec`](../../../model/phases/start_kernel/early_boot.spec)。
