# MemBlock 模型与实现设计

## 冻结范围

本里程碑新增一个 Kernel-owned `MemBlock`，以及两个独立子对象
`MemBlockMemory`、`MemBlockReserved`。三个对象均从 Ready 经唯一 Enable 进入 Online；模型只
跟踪来源、完成状态与启动顺序，不建立属性字段、Relation、Map、Collection 或区间对象。

对象树与阶段顺序为：

```text
Kernel
└── MemBlock
    ├── MemBlockMemory
    └── MemBlockReserved

EarlyBoot / Enter (parse_dtb and bootmem)
  -> MemBlockMemory.Enable
  -> MemBlockReserved.Enable
  -> MemBlock.Enable
  -> SwapperPageTable.Enable
```

Memory 消费 Online 的 DtbBlob 和 QEMU 提供的有效、非空 `/memory` 内容事实，并建立
`memblock_memory_derived_from_dtb(MemBlockMemory, DtbBlob)`。Reserved 在 Memory Online 后
消费 Ready 的 KernelImage、有效的 DTB 文件自身范围以及有效的 reserve map/
`/reserved-memory` 内容事实，建立一个完整 reservation 事实。Reserved Online 以单一状态
覆盖 KernelImage、DTB 文件自身、FDT reserve map 与 `/reserved-memory`，不逐范围展开。

## 三类范围边界

- DTB 文件自身范围是固件输入 blob 的物理存储，DtbBlob Enable 用它验证可读取输入；
- DTB `/memory` 描述的是系统 RAM，MemBlockMemory 的 Online 与 provenance 表示它已被观察；
- MemBlock Reserved 表示不得分配的强制保留已经完整提交，来源包括内核镜像和两类 DTB
  reservation 输入。

QemuVirtPlatform 只发布其 DTB 内容有效的输入事实，不能替代 DtbBlob Online 或 MemBlock 的
provenance/completion。来源严格单向为 `DtbBlob → MemBlock`。

## 启动阶段与失败提交

```text
EarlyBoot:
  Banner → DtbBlob → MemBlockMemory → SbiCapability → EarlyConsole
EarlyBoot Enter 后续 drive:
  MemBlockReserved → MemBlock → SwapperPageTable → Scheduler → Unmask
```

Memory 输入缺失时三个 MemBlock 对象都保持 Ready，EarlyBoot Enter 不继续 capability 或 Console。
Reserved 输入缺失时，已经完成的 Memory、capability 与 Console 保持 Online，Reserved 与父
MemBlock 保持 Ready；EarlyBoot Enter 不继续 SwapperPageTable、Scheduler、Unmask 或 BootSetup。

MemBlock Online 本身就是早期物理内存分配的状态前提，不增加同义
`ready_for_allocation` predicate。

## 实现边界

当前 Rust 已实现固定容量的 DTB `/memory` 扫描、FDT reserve map、静态
`/reserved-memory/reg`、KernelImage 与 DTB 自身保留，以及 `setup_vm_final` 的 SwapperPageTable
构造和切换；M1 成功后保持中断屏蔽停驻。Scheduler、Unmask、buddy allocator 和 NUMA 仍留给
后续里程碑。

实现 checkpoint 现已覆盖 MemBlock 的 11 条 invariant 与 2 条 establishes。Memory/Reserved
在 `setup_bootmem` 返回且页表动态分配尚未开始的边界生成规范化序列、count 和 FNV-1a digest；
runner 对摘要和完整旁路序列双重校验，并在失败时报告首个差异。Reserved 差分使用排除各自
KernelImage 具体长度的不可分配投影，以保持二进制大小不进入跨实现 ABI；Linux 的 NOMAP
memory region 同时纳入该投影。

NUMA、hotplug、NOMAP、memory limit、动态 `/reserved-memory` 分配、区间裁剪/对齐和实际分配
策略仍留给后续里程碑；容器容量、排序与合并是 coding/impl 细节，不进入 model。
