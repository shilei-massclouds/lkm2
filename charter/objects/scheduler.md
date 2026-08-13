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

- `Scheduler` 声明 `sched_core: true`；model 只定义 Scheduler 生命周期、
  Schedule 的信号因果和 `switches` 发生的位置。推导器为每个 Scheduler
  实例附加 idle Task 和私有 runq 上下文，但 Scheduler 不拥有线路 current。
- `Cpu0Scheduler.idle_task` 固定引用 `BootTask`。推导器用它预置 CPU0 线路的
  `CurrentTaskRef`，但初始化之后 idle 与 current 完全独立。

## 隐藏 runq 的集合语义

- runq 不是 model 对象、字段、`Collection` 或 selector，model 不能读取、遍历、
  排序或更新它。它是推导器为每个 Scheduler 实例维护的隐藏状态。
- runq 是 Task 对象身份的集合，不是 FIFO 或任何其他有序队列。对一个
  CPU，它表示该 CPU 上全部 runnable 的普通 Task，而不是“runnable 但当前
  未运行”的 Task。因此普通 current Task 在 runnable 期间仍是 runq 成员；
  `CurrentTaskRef` 与 runq membership 是正交坐标。
- idle Task 是 runq 为空时的线路 fallback，不是普通 runq 成员。
- sched_core 隐式提供无参数 `Action::Enqueue`、`Action::Dequeue`。signal source
  必须是 Task；两者分别表示 Task 进入 runnable 集合和离开 runnable 集合，
  Scheduler 仅在 `State::Online` 处理它们。重复加入和删除不存在的成员均失败。
- `Online` 与 `OnCpu` 都是 runnable Task 状态。`Suspend` 只从 running 切换到
  非 running，`Resume` 只从非 running 切换到 running；两者都不改变 runq
  membership。`switches` 本身也不改变 runq。

## `switches` 的全候选展开

- `switches next_task_ref;` 必须严格按 runq 快照是否为空分成两种、且仅两种处理：

  1. runq 非空：对其中每一个 Task 各建立一条隔离推导线路，不得
     漏掉、过滤或优先选择任何成员；idle Task 不是此时的候选。
  2. runq 为空：只建立一条隔离推导线路，将绑定指向该 Scheduler 的
     idle Task。这是 fallback，不是从 runq 选出 idle。
- 选中 idle 不得将它加入 runq，后续 Suspend/Resume 也不得改变这一点。
  推导器必须拒绝任何试图将 Scheduler 的 idle Task 加入 runq 的操作；
  idle 始终位于 runq 之外。
- 不存在可以暂时排除 current 的“策略候选结构”，也不存在与 runq 并立的
  第二套可选 Task 集合。只要普通 current 仍 runnable，它就在 runq 中，
  `switches` 也必须展开“仍选中 current”的线路。
- 为保证可重复的 JSON 或 CLI 输出，推导器可以用稳定顺序序列化集合成员和
  推导线路；该顺序只是规范化细节，不具有 FIFO、优先级或调度策略语义，
  model 不得观测或依赖它。

## current 与切换事务

- `CurrentTaskRef` 是 CPU 推导线路上下文的动态只读 Task selector，不属于
  Scheduler。Schedule 使用它之前必须确认其指向的 Task 为 `State::OnCpu`；否则返回
  `invalid_current_task_ref`，不得执行 Suspend、switches 或 TaskFlow。
- 每个 Task 必须有且仅有一个以它为 `parent` 的 TaskFlow。具名 TaskFlow 不得被直接
  Enter；Task handler 通过 `self.TaskFlowRef` 或 `self.ResumeTargetRef` 表达 ownership。
  是否在 Resume 后进入恢复点由 Resume handler 的 model-declared `resumes` 决定。
- `switches` 不隐式执行 Suspend 或 Resume。Suspend、Resume 以及 Schedule
  handler 校验全部成功后才提交 current；任一失败均保留旧 current，并丢弃该 Resume
  handler 延迟的 resumes。提交后才执行这些 resumes；恢复入口失败不回滚已提交的
  current 和调度切换。
- `Task.Enable` 使普通 Task 首次进入 runnable 集合，因而触发 Enqueue。
  Suspend/Resume 不触发 Enqueue/Dequeue；未来的 Block、Exit 或其他离开 runnable
  语义才触发 Dequeue，对应的 Wakeup/Enable 语义触发 Enqueue。
- `BootSetup` 只把 Scheduler 推进 Online，并完成 `KernelInitTask` 的 Preset、Setup
  与 Enable。在没有 Block/Exit 的当前切片中，`KernelInitTask` 成为 current 后仍保留在
  runq 中。

## 后续 ownership

引入 `CpuGroup` 与 CPU 对象后，必须把当前实例物化并绑定为 `CpuGroup.cpus[0]` owned 的 Scheduler；新增的每个 CPU 必须分别拥有独立 Scheduler，不得复用 `Cpu0Scheduler`，也不得把 `Scheduler` 类型当作实例。

临时模型映射：[model/objects/scheduler.spec](../../model/objects/scheduler.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
