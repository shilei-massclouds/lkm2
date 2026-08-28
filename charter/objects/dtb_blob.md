# DtbBlob 章程

`DtbBlob` 是 Kernel 持有的固件设备树输入对象。它的身份来自 OpenSBI 按 RISC-V boot ABI
传入的 DTB 物理地址，但它不拥有固件内存，也不负责建立虚拟映射；ArchHead 与 Vm 负责在
进入 StartKernel 前提供稳定的只读访问窗口。

`DtbBlob` 初始为 Ready。唯一的 `Enable: Ready → Online` 必须观察唯一
`/chosen/bootargs`，从 `ChosenBootArgs` 取得唯一 `earlycon` 值，并把同一个值提交到
Kernel-owned `BootCommandLine`。缺失或重复 bootargs、属性畸形、命令行不合法以及值不唯一
都不得令对象进入 Online，也不得建立部分 `BootCommandLine` 内容。

Online 只表示所需 bootargs 已经完成校验、观察和复制，不表示 DTB 映射被撤销、固件内存
失效或其他设备树属性已经建立生命周期。当前切片只消费 `/chosen/bootargs`；未来增加 CPU、
memory 或 device 节点消费者时，应扩展 DtbBlob 的明确输出边界，而不是把解析副作用隐含在
EarlyConsole 中。

模型映射：
[`model/objects/dtb_blob.spec`](../../model/objects/dtb_blob.spec)。
