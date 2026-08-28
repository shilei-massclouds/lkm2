# QEMU virt 平台章程

`QemuVirtPlatform` 是默认机器输入的生产者。其 `Enable: Ready → Online` 在向 OpenSBI
发出 Enable 之前，建立 `DtbBlob` 的物理范围事实：范围大小至少为 1，且范围有效。Online
状态持续保持这两项事实。

范围有效至少表示起止边界有序、长度非零且地址加法不溢出；具体 QEMU 内存布局仍属于平台
配置。OpenSBI、Kernel 和 DtbBlob 都不得替代平台生产这些事实，DtbBlob Enable 只消费它们。

模型映射：[model/systems/qemu_virt_platform.spec](../../model/systems/qemu_virt_platform.spec)

章程与模型的关系见[系统章程](../main.md)。
