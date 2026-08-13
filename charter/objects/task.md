# Task 与 BootTask 章程

## 生命周期

可调度 Task 的运行生命周期固定为：

```text
Online --Resume--> OnCpu --Suspend--> Online
```

`Transition::Resume` 只能在 `State::Online` 声明或 override，且目标只能是
`State::OnCpu`。Task 不得在 `OnCpu` 或其他状态处理 Resume，也不得用 Resume
自迁移。普通 Task 的 Enable 与 Suspend 会 Enqueue，Resume 会 Dequeue。

## BootTask

`BootTask` 初始为 `Online`。CPU0 推导线路构造时，推导器从
`Cpu0Scheduler.idle_task` 将线路 `CurrentTaskRef` 预置为 BootTask；这是启动早期
唯一允许 current 指向 Online Task 的时刻。Kernel 首次驱动 BootTask Resume 后，
BootTask 才进入 OnCpu，随后推导器进入其唯一 `BootInitFlow`。

BootTask 兼作 idle Task，因此对生命周期有两处 override：

- `Online --Resume--> OnCpu` 不 Dequeue，适用于首次启动和 idle 恢复；
- `OnCpu --Suspend--> Online` 不 Enqueue。

临时模型映射：[model/objects/task.spec](../../model/objects/task.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
