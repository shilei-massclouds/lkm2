# EarlyBoot 编码约束

本页对应
[`model/phases/start_kernel/early_boot.spec`](../../../model/phases/start_kernel/early_boot.spec)。
M5 将 [`impl/phases/start_kernel.rs`](../../../impl/phases/start_kernel.rs) 实现到 EarlyConsole
注册并回放 banner 后的 interrupt-masked 停驻；Scheduler、真实 runtime trap、IRQ/timekeeping
初始化和硬件 Unmask 留给后续实现阶段。

## 固定控制边界

`EarlyBoot.Action::Enter` 覆盖从 `start_kernel` 到全局 S-mode interrupt enable 成功的范围：

1. 复查 BootTask 是 current 且 OnCpu、`Cpu0Scheduler.state == Ready`，并确认 ArchHead
   交接时 interrupts masked；
2. 驱动 `Banner.Transition::Enable`，通过初始 Online 的 Printk 提交内核 banner；此时
   Console 可以尚未 Online；
3. 驱动 `DtbBlob.Transition::Enable`，从其 `/chosen/bootargs` 子关系把唯一的
   `earlycon` 值复制到静态存在、初始为空的 `BootCommandLine`；
4. 驱动 `SbiCapability.Transition::Enable`，对应 `sbi_init()` 提交探测完成状态及实际可用的
   SBI console capability；
5. 驱动 `EarlyConsole.Transition::Enable`，依次查询命令行和静态 earlycon 注册表，
   静态嵌套驱动 `SbiConsole.Transition::Enable`，确认查询所得 backend 已 Online 后原子建立
   backend 字段、binding 和 Printk Console 注册事实；
6. 驱动 `Cpu0Scheduler.Transition::Enable`，使 CPU0 Scheduler 进入 Online；
7. 最后驱动 `CurrentCPU.InterruptControlRef.Action::Unmask`；
8. 仅在 Banner、DtbBlob、SbiCapability、EarlyConsole、Scheduler 均为 Online，命令行存在
   `earlycon` 键，且 backend binding 与 Printk 注册事实均已由 EarlyConsole 提交后，建立
   `early_boot_interrupts_enabled()`；EarlyBoot 不约束该键的具体外部输入值。

Unmask 必须始终是 EarlyBoot 的最后一个 drive；任何后续引入的 architecture、memory、
trap、IRQ、timer 或 timekeeping 初始化都只能插入它之前，并需要独立冻结 ownership、状态
与失败语义。Unmask 失败时不得发布成功事实或进入 BootSetup，且必须维持 interrupts
关闭的 fail-stop 路径。

已完成的 M2 直接用 `DtbBlob.Enable` 表达“内核观察并复制 bootargs”的生命周期边界，不增加
`SetupArch` 或 `parse_dtb` 具名阶段，也不把字符串扫描算法纳入 model。DtbBlob binding
失败时，Banner 已经 Online，但后续 EarlyConsole、Scheduler 和 Unmask drive 均不得发生；
Online 不表示 DTB 内存失效。

已完成的 M3 在 Kernel Setup 中提前完成 `EarlyConTable.Link`；EarlyBoot 不驱动 Link。
已完成的 M4 在 DtbBlob 与 EarlyConsole 之间增加 `SbiCapability.Enable`，EarlyBoot 因此具有六个
直接 drive，并冻结 Banner → DtbBlob → SbiCapability → EarlyConsole → Scheduler → Unmask
顺序。
到 `EarlyConsole.Enable` 查询 registry 时，Kernel Setup 已保证表为 Ready 且包含 SBI 条目。
正式表只含 SBI backend，因此 nested drive 是静态目标；当前编译器的动态 signal target
范围不包含 relation/map binding，禁止把 `backend` binding 直接用作 signal target。

若 capability 探测完成却没有有效 transport，`SbiConsole.depends_on` 必须失败；若唯一选择或
上游 backing invariant 失败，或前置 binding 失败，
`EarlyConsole` 不提交 backend 字段、binding 或 Printk 注册事实，Scheduler、Unmask 与
BootSetup 均不得继续；已经完成的 Banner、DtbBlob 与 SbiCapability 前序 drive 及其
availability 事实保留，失败的 `SbiConsole` 子 drive 不提交 Online 或 selection 事实。

BootSetup 入口复查 `early_boot_interrupts_enabled()`、BootTask current/OnCpu 和 Scheduler
Online，之后只驱动 `KernelInitTask` 的 Preset、Setup、Enable。BootHandoff 仍拥有首次
Scheduler Schedule 和第一次上下文切换。

全局 interrupt enable 不代表设备 interrupt source 已启用，也不把 ArchHead 安装的
StartKernel-safe early fail-stop trap 提升为 runtime dispatch。Linux 的
architecture/memory → trap/core memory → scheduler → IRQ/timer/timekeeping →
`local_irq_enable` 仅作粗粒度审阅锚点，不是本轮模型冻结的逐调用顺序。

## M5 运行时前缀

M5 保持 `setup_vm(dtb_pa) -> usize` 的链接 ABI；VM 另行发布 DTB 映射虚址及两段 PMD 中从
DTB 起算的剩余只读窗口。FDT parser 必须完整校验 header、各 block 范围、reservation map、
structure token 和字符串引用，只复制唯一 `/chosen/bootargs`。命令行固定最多 4096 字节，
要求 NUL 结尾、无内嵌 NUL 且为 UTF-8，并且恰好包含一个独立 `earlycon=sbi` token。

上述 DTB 输入与 BootCommandLine 的生产边界独立映射在
[`coding/objects/dtb_blob.md`](../../objects/dtb_blob.md)。

backend 必须由链接脚本保留的 `EarlyConTable` 唯一查询产生，不能因当前只有 SBI backend
而跳过 registry。后续 capability probe 只接受 SBI >= 2.0 且 DBCN probe 为正；Rust 不实现
正式模型仍保留的 SBI v0.1 fallback。任何输入、查询、探测、输出或注册失败都保持中断屏蔽
并 fail-stop，不得继续 Scheduler、Unmask 或 BootSetup。

实际 StartKernel 前缀严格执行：先把固定 banner 交给 Printk，再取得 DTB 映射并建立
`BootCommandLine`，查询链接期 backend，依次构造 `SbiCapability` 与 `SbiConsole`，最后由
Printk 注册并回放。DBCN 输出使用 `CONSOLE_WRITE_BYTE`，避免早期虚址到物理地址转换；每次
SBI error 都立即终止链。默认 QEMU bootargs 是 `earlycon=sbi`，smoke runner 不启用也不
检查 checkpoint debugcon。
