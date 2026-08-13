# Kernel 章程

Kernel 完成 `Preset → Setup → Enable → Online` 生命周期。Kernel Enable 必须驱动
`BootTask.Transition::Resume`；CPU0 线路此时已把 `CurrentTaskRef` 预置为 Online 的
BootTask。Resume 成功地把 BootTask 转为 OnCpu 后，推导器进入其唯一
`BootInitFlow`，不得通过 OnCpu Resume 自迁移绕过首次启动语义。

Kernel 本身不拥有或修改 `CurrentTaskRef`。启动信号与 Task 生命周期属于 model
声明；线路 current 的维护和成功 Resume 后的 TaskFlow 进入属于推导器职责。

临时模型映射：[model/systems/kernel.spec](../../model/systems/kernel.spec)

章程与模型的关系见[系统章程](../main.md)。
