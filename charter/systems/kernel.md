# Kernel 章程

Kernel 完成 `Preset → Setup → Enable → Online` 生命周期。Kernel Enable 必须驱动
`BootTask.Transition::Resume`；CPU0 线路此时只保证 `CurrentCPU == BootCPU`，
`CurrentTaskRef` 仍不可用。Resume 成功地把 BootTask 转为 OnCpu 后，其 model-declared
`resumes self.ResumeTargetRef.Action::Enter` 解析并进入唯一 `BootInitFlow`，不得通过
OnCpu Resume 自迁移绕过首次启动语义。

Kernel 本身不拥有或修改 `CurrentTaskRef`。ArchHead 随后以 BootTask 的
`Action::ResetCurrent` 完成唯一 bootstrap 发布。启动信号与 Task 生命周期属于 model
声明；model 决定 Resume 后是否进入恢复点，推导器负责 selector 解析、线路 current
维护和 Scheduler 切换事务。

临时模型映射：[model/systems/kernel.spec](../../model/systems/kernel.spec)

章程与模型的关系见[系统章程](../main.md)。
