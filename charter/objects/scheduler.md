# Scheduler 章程

## 目标语义

Scheduler 是 per-CPU 运行对象，不是整个内核共享的全局单例。每个逻辑 CPU 必须拥有独立的 Scheduler 身份、生命周期和调度状态；一个确定性的中央事件循环可以串行推导这些对象，但不改变模型中的 ownership。

## 当前 CPU0-only 切片

当前只物化 `Cpu0Scheduler`，表示 BootCPU/逻辑 CPU0 的 Scheduler。CPU 与 `CpuGroup` 尚未进入模型，因此本轮不为它声明临时 `parent`，也不把缺失的 ownership 错写成全局所有权。

`Cpu0Scheduler` 的最小生命周期是：

```text
Ready --BootSetup/Enable--> Online
Online --Schedule--> Suspend(current) → selects next → Resume(next)
```

- `Scheduler` 声明 `sched_core: true`，模型只定义 Schedule 策略与生命周期；
  current Task、idle Task 和实例私有 runq 由推导器按路径维护，不再建模为字段、
  `Collection` 或 `TaskRef` 对象。
- `Cpu0Scheduler.idle_task` 固定引用 `BootTask`。初始 current 与 idle 都是
  `BootTask`，runq 为空。
- sched_core 隐式提供无参数 `Action::Enqueue`、`Action::Dequeue`。signal source
  必须是 Task；runq 保存 Task 对象身份、保持唯一 FIFO，且 Scheduler 仅在
  `State::Online` 处理这两个信号。
- `selects next_task_ref;` 对当前 runq 的每个成员按 FIFO 建立隔离推导路径；空
  runq 使用 idle Task。选择不自动出队，后续 Resume 通过 Task 生命周期触发
  Dequeue。
- `CurrentTaskRef` 是当前 Scheduler 路径上下文的动态 Task target；它不是字段或
  可声明对象。Schedule 严格执行 Suspend(current)、selects、Resume(next)，全部
  成功后才提交 current。
- 每个 Task 必须有且仅有一个以它为 `parent` 的 TaskFlow。Schedule 提交并完成
  普通 emits/resumes 后，推导器隐式恢复所选 TaskFlow 的 `Action::Enter`。
- `Task.Enable` 和 `Task.Suspend` 触发 Enqueue，`Task.Resume` 触发 Dequeue；
  `BootTask` 作为 idle Task override Resume/Suspend，不操作 runq。
- `BootSetup` 只把 Scheduler 推进 Online，并完成 `KernelInitTask` 的 Preset、Setup
  与 Enable。默认推导最终 current 为 `KernelInitTask`、runq 为空。

## 后续 ownership

引入 `CpuGroup` 与 CPU 对象后，必须把当前实例物化并绑定为 `CpuGroup.cpus[0]` owned 的 Scheduler；新增的每个 CPU 必须分别拥有独立 Scheduler，不得复用 `Cpu0Scheduler`，也不得把 `Scheduler` 类型当作实例。

临时模型映射：[model/objects/scheduler.spec](../../model/objects/scheduler.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
