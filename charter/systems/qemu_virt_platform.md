# QEMU virt 平台章程

`QemuVirtPlatform` 是默认机器输入的生产者。其 `Enable: Ready → Online` 在向 OpenSBI
发出 Enable 之前，建立四项 `DtbBlob` 输入事实：DTB 文件自身物理范围大小至少为 1 且范围
有效；DTB 描述了非空且有效的物理内存；DTB 的 FDT reserve map 与 `/reserved-memory`
描述有效。Online 状态持续保持这四项事实。

DTB 文件自身范围有效至少表示起止边界有序、长度非零且地址加法不溢出；它不同于
`/memory` 描述的系统 RAM。后两项事实只描述 QEMU 提供的 DTB 内容有效，不表示 MemBlock 已经
观察或提交这些内容。具体 QEMU 内存布局仍属于平台配置。OpenSBI、Kernel 和 DtbBlob 都不得
替代平台生产这些事实；DtbBlob Enable 消费文件自身范围事实，MemBlock 在 DtbBlob Online 后
消费内容事实并建立自己的 provenance/completion 输出。

MemBlock Reserved 维护的强制保留集合同时覆盖 KernelImage、DTB 文件自身、FDT reserve map
和 `/reserved-memory`；QemuVirtPlatform 的内容事实不单独表示这些保留已经完成。

模型映射：[model/systems/qemu_virt_platform.spec](../../model/systems/qemu_virt_platform.spec)

章程与模型的关系见[系统章程](../main.md)。
