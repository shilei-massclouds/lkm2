# MemBlock 章程

`MemBlock` 是 Kernel 持有的早期物理内存状态对象。它拥有两个具有独立生命周期的子对象：
`MemBlockMemory` 表示可用物理内存已经从 DTB `/memory` 描述导出，
`MemBlockReserved` 表示启动所需的强制保留已经完整纳入。三个对象都初始为 Ready，且都只支持
唯一的 `Enable: Ready → Online`。

## 生命周期与来源

两个子对象由各自所属的启动阶段驱动，而不是由父对象嵌套驱动：`parse_dtb` 对应的
EarlyBoot 边界驱动 `MemBlockMemory.Enable`，`paging_init()` 中的 `setup_bootmem()` 边界驱动
`MemBlockReserved.Enable`。随后 `MemBlock.Enable` 只检查两个子对象都已 Online，才提交父对象
Online。这一拆分保留 Memory 与 Reserved 的独立失败和提交语义。

`MemBlock` Online 直接表示早期物理内存分配所需的内存发现和强制保留前提已经满足，不得再建立
`ready_for_allocation` 或其他同义事实。

`MemBlockMemory.Enable` 必须消费 Online 的 `DtbBlob`，以及平台提供的“DTB 描述了非空且有效的
物理内存”事实。成功时建立
`memblock_memory_derived_from_dtb(MemBlockMemory, DtbBlob)`；Online invariant 持续保留这一
单向来源。`DtbBlob → MemBlockMemory` 是唯一来源方向，MemBlock 不改变 DtbBlob 状态，也不向
DtbBlob 反向发布内容事实。

`MemBlockReserved.Enable` 必须消费 Online 的 `MemBlockMemory` 与 `DtbBlob`、Ready 的
`KernelImage`、有效的 DTB 文件自身物理范围，以及平台提供的“FDT reserve map 与
`/reserved-memory` 描述有效”事实。成功时建立
`memblock_required_reservations_complete(MemBlockReserved, KernelImage, DtbBlob)`。
`MemBlockReserved` Online 一次性表示以下强制保留均已完整纳入：KernelImage、DTB 文件自身、
FDT reserve map 条目和 `/reserved-memory` 描述；模型不为这些来源分别创建范围对象或事实。

Memory 成功而 Reserved 失败时，`MemBlockMemory` 保持 Online，`MemBlockReserved` 与
`MemBlock` 保持 Ready；此前已经完成的 SBI capability 与 EarlyConsole 也保持 Online，但
`setup_vm_final`、Scheduler、interrupt Unmask 和 BootSetup 不得执行。Memory 自身失败时三个
MemBlock 对象都保持 Ready，EarlyBoot 不得继续 SBI capability 或 Console。

## 抽象边界

三个对象均没有属性字段，也不使用 Relation、Map、Collection 或其他范围容器。模型不记录
物理区间、段数、排序、裁剪、对齐、合并算法或底层数据结构，只冻结来源、完成状态和启动顺序。
DTB 文件自身的物理存储范围、DTB `/memory` 描述的系统 RAM，以及
`MemBlockReserved` 表示的强制保留范围是三个不同概念，不得互相替代。

当前 Rust 前缀实现 DTB `/memory` 扫描和 `setup_bootmem` 的强制保留提交；具体容器、容量、
排序与合并仍属于 coding/impl，不能反向成为 model 状态。NUMA、hotplug、NOMAP、memory
limit、动态 `/reserved-memory` 分配、区间裁剪/对齐以及实际分配 API 与策略留给后续里程碑。

模型映射：[model/objects/memblock.spec](../../model/objects/memblock.spec)。
