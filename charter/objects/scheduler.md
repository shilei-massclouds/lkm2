# Scheduler 章程

## 目标语义

Scheduler 是 per-CPU 运行对象，不是整个内核共享的全局单例。每个逻辑 CPU 必须拥有独立的 Scheduler 身份、生命周期和调度状态；一个确定性的中央事件循环可以串行推导这些对象，但不改变模型中的 ownership。

## 当前 CPU0-only 切片

当前只物化 `Cpu0Scheduler`，表示 BootCPU/逻辑 CPU0 的 Scheduler。CPU 与 `CpuGroup` 尚未进入模型，因此本轮不为它声明临时 `parent`，也不把缺失的 ownership 错写成全局所有权。

`Cpu0Scheduler` 的最小生命周期是：

```text
Ready --BootSetup/Enable--> Online
Online --Schedule--> validate line current → Suspend(current) → switches next → Resume(next)
```

- `Scheduler` 声明 `sched_core: true`，模型只定义 Schedule 策略与生命周期；
  Scheduler runtime 只维护 idle Task 和实例私有 runq，不拥有 current。
- `Cpu0Scheduler.idle_task` 固定引用 `BootTask`。推导器用它预置 CPU0 线路的
  `CurrentTaskRef`，但初始化之后 idle 与 current 完全独立。
- sched_core 隐式提供无参数 `Action::Enqueue`、`Action::Dequeue`。signal source
  必须是 Task；runq 保存 Task 对象身份、保持唯一 FIFO，且 Scheduler 仅在
  `State::Online` 处理这两个信号。
- `switches next_task_ref;` 对当前 runq 的每个成员按 FIFO 建立隔离推导路径；空
  runq 绑定 idle Task。switch 不自动出队，后续 Resume 通过 Task 生命周期触发
  Dequeue。
- `CurrentTaskRef` 是 CPU 推导线路上下文的动态只读 Task selector，不属于
  Scheduler。Schedule 使用它之前必须确认其指向的 Task 为 `State::OnCpu`；否则返回
  `invalid_current_task_ref`，不得执行 Suspend、switches 或 TaskFlow。
- 每个 Task 必须有且仅有一个以它为 `parent` 的 TaskFlow。具名 TaskFlow 不得被直接
  Enter；Task handler 通过 `self.TaskFlowRef` 或 `self.ResumeTargetRef` 表达 ownership。
  是否在 Resume 后进入恢复点由 Resume handler 的 model-declared `resumes` 决定。
- `switches` 不隐式执行 Suspend 或 Resume。Suspend、Resume、Dequeue 以及 Schedule
  handler 校验全部成功后才提交 current；任一失败均保留旧 current，并丢弃该 Resume
  handler 延迟的 resumes。提交后才执行这些 resumes；恢复入口失败不回滚已提交的
  current 和调度切换。
- `Task.Enable` 和 `Task.Suspend` 触发 Enqueue，`Task.Resume` 触发 Dequeue；
  `BootTask` 作为 idle Task，首次/idle Resume 与 Suspend override 均不操作 runq。
- `BootSetup` 只把 Scheduler 推进 Online，并完成 `KernelInitTask` 的 Preset、Setup
  与 Enable。默认推导最终 current 为 `KernelInitTask`、runq 为空。

## 后续 ownership

引入 `CpuGroup` 与 CPU 对象后，必须把当前实例物化并绑定为 `CpuGroup.cpus[0]` owned 的 Scheduler；新增的每个 CPU 必须分别拥有独立 Scheduler，不得复用 `Cpu0Scheduler`，也不得把 `Scheduler` 类型当作实例。

临时模型映射：[model/objects/scheduler.spec](../../model/objects/scheduler.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
