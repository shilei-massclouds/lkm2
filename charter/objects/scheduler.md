# Scheduler 章程

## 目标语义

Scheduler 是 per-CPU 运行对象，不是整个内核共享的全局单例。每个逻辑 CPU 必须拥有独立的 Scheduler 身份、生命周期和调度状态；一个确定性的中央事件循环可以串行推导这些对象，但不改变模型中的 ownership。

## 当前 CPU0-only 切片

当前只物化 `Cpu0Scheduler`，表示 BootCPU/逻辑 CPU0 的 Scheduler。CPU 与 `CpuGroup` 尚未进入模型，因此本轮不为它声明临时 `parent`，也不把缺失的 ownership 错写成全局所有权。

`Cpu0Scheduler` 的最小生命周期是：

```text
Ready --BootSetup/Enable--> Online
Online --Schedule--> panic "impl sched"
```

- Scheduler 固定维护 `mutable curr: TaskRef`、`mutable idle: TaskRef` 和
  `runq: Collection<TaskRef>`；CPU0 实例初始 curr/idle 都是 `BootTaskRef`，runq
  指向 owned 的 `Cpu0RunQ`。
- `SetIdleTask(task_ref)` 与 `SetCurrentTask(task_ref)` 分别只更新对应字段。
- `Enqueue(task_ref)` 委托 `Cpu0RunQ.Action::Enqueue(task_ref)`；队列保持唯一 FIFO。
- `BootSetup` 先将 Scheduler 推进到 Online，再显式设置 BootTaskRef，并让
  `KernelInitTask.Enable(KernelInitTaskRef)` 把 KernelInitTaskRef 入队。BootTaskRef
  是直接维护的 current/idle，不进入 RunQ。
- `Schedule` 仍是占位 panic。本轮不定义出队、PickNext、curr 切换、idle fallback、
  run token、其它 CPU Scheduler 或通用任务选择策略。

## 后续 ownership

引入 `CpuGroup` 与 CPU 对象后，必须把当前实例物化并绑定为 `CpuGroup.cpus[0]` owned 的 Scheduler；新增的每个 CPU 必须分别拥有独立 Scheduler，不得复用 `Cpu0Scheduler`，也不得把 `Scheduler` 类型当作实例。

临时模型映射：[model/objects/scheduler.spec](../../model/objects/scheduler.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
