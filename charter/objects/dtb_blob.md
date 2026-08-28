# DtbBlob 章程

`DtbBlob` 是 Kernel 持有的固件设备树输入对象。它的身份来自 OpenSBI 按 RISC-V boot ABI
传入的 DTB 物理地址，但它不拥有固件内存，也不负责建立虚拟映射；ArchHead 与 Vm 负责在
进入 StartKernel 前提供稳定的只读访问窗口。

`DtbBlob` 初始为 Ready。唯一的 `Enable: Ready → Online` 必须观察唯一
`/chosen/bootargs`，从 `ChosenBootArgs` 取得唯一 `earlycon` 值，并把同一个值提交到
Kernel-owned `BootCommandLine`。Enable 还必须消费 QemuVirtPlatform 已建立的 DTB 物理范围
事实：范围大小至少为 1，且起止边界、长度及地址运算有效。范围事实缺失、空范围、非法范围、
缺失或重复 bootargs、属性畸形、命令行不合法以及值不唯一都不得令对象进入 Online，也不得
建立部分 `BootCommandLine` 内容。

Online 只表示所需 bootargs 已经完成校验、观察和复制，不表示 DTB 映射被撤销、固件内存
失效，也不表示 `/memory` 或 reservation 描述已经被 DtbBlob 自身解析并提交。物理范围的
存在性与有效性由 QemuVirtPlatform 生产，DtbBlob 只消费，不得自行建立并自检。

必须区分三类范围语义：DTB 文件自身占用的物理存储范围由
`dtb_blob_physical_range_*` 事实描述；DTB `/memory` 节点描述的是系统 RAM 输入；
MemBlock Reserved 表示内核镜像、DTB 文件自身、FDT reserve map 和 `/reserved-memory` 所要求的
强制保留。后两类不是 DtbBlob 对象的存储范围。QemuVirtPlatform 可以建立关于 DTB 内容有效性的
平台输入事实，但 `MemBlockMemory` 仍必须在 DtbBlob Online 后建立明确的
`memblock_memory_derived_from_dtb` 来源，不能把平台事实当作 MemBlock 已完成。

当前 DtbBlob 生命周期只向 `BootCommandLine` 发布 `/chosen/bootargs`；MemBlock 作为独立消费者
读取 `/memory` 与 reservation 相关的有效性事实。未来增加 CPU 或 device 节点消费者时，应继续
扩展明确的单向输出边界，而不是把解析副作用隐含在 EarlyConsole 或 DtbBlob Online 中。

模型映射：
[`model/objects/dtb_blob.spec`](../../model/objects/dtb_blob.spec)。
MemBlock 的来源与完成边界见 [MemBlock 章程](memblock.md)。
