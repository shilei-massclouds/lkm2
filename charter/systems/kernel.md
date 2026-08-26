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

Kernel Setup 必须驱动 `KernelImage` 子对象 `EarlyConTable` 的唯一
`Link: Base → Ready`，自身只建立
`ChosenBootArgs.contains("earlycon", "sbi")` relation effect。只有 Link 已令表进入 Ready
且建立 `EarlyConTable.contains("sbi", SbiConsole)` 后，Kernel 才能提交 Ready；Kernel
不得直接代替 Link 建立该注册 tuple。若 Link 无法满足 Ready invariant，Setup 必须
fail-stop，后续 Kernel Enable 与 EarlyBoot 均不得执行。

临时模型映射：[model/systems/kernel.spec](../../model/systems/kernel.spec)

章程与模型的关系见[系统章程](../main.md)。
