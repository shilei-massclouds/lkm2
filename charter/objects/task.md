# Task 与 BootTask 章程

## 生命周期

可调度 Task 的运行生命周期固定为：

```text
Online --Resume--> OnCpu --Suspend--> Online
```

`Transition::Resume` 只能在 `State::Online` 声明或 override，且目标只能是
`State::OnCpu`。Task 不得在 `OnCpu` 或其他状态处理 Resume，也不得用 Resume
自迁移。`Online` 和 `OnCpu` 均表示 runnable；两者的区别只是是否当前在 CPU
上运行。因此普通 Task 的 Enable 会 Enqueue，Suspend 与 Resume 均不改变隐藏
runq membership，也不得触发 Enqueue 或 Dequeue。

## BootTask

`BootTask` 初始为 `Online`。CPU0 推导线路构造时只发布 `CurrentCPU`，线路
`CurrentTaskRef` 仍不可用。Kernel 首次驱动 BootTask Resume 后，BootTask 进入 OnCpu；
其 Resume override 通过
`resumes self.ResumeTargetRef.Action::Enter` 进入唯一 `BootInitFlow`。

BootTask 在 `State::OnCpu` 声明 bootstrap-only `Action::ResetCurrent`。ArchHead 驱动
该 Action 时，推导器先确认 BootTask 的 TaskFlow 已绑定线路 `CurrentCPU`，handler
完整成功后才首次发布 `CurrentTaskRef = BootTask`。ResetCurrent 不清理 Task 数据、
不初始化 stack，也不是普通调度切换；重复调用或失败调用必须保留原 current。

`self.TaskFlowRef` 是 Task handler 可用的静态只读 selector，始终指向该 Task 唯一的
parent TaskFlow。`self.ResumeTargetRef` 是动态只读恢复坐标：没有 parked 用户态
episode 时等于 TaskFlowRef，有 parked `UserAppRuntime` 时指向该 Runtime。两者只允许
以无参数 `resumes ...Action::Enter` 使用，不是字段，也不能声明、赋值或更新。
Task 派生对象可以 override Resume 并省略 resumes，以显式选择 Resume 后不进入恢复点。

BootTask 兼作 runq 为空时的 idle fallback，不是普通 runq 成员。它的 Resume
仍显式声明 `resumes self.ResumeTargetRef.Action::Enter`。“Resume 不 Dequeue、
Suspend 不 Enqueue”不是 BootTask 的特例，而是所有 Task 的通用规则。

## CPU 绑定与 Exit

每个 TaskFlow 保存当前 CPU 的 mutable `cpu_ref`。线路构造时，推导器将 BootTask 的
TaskFlow 绑定 BootCPU，使 BootTask 可以在 current 尚不可用时先 Resume 并进入
BootInitFlow；后续调度切换提交时由推导器绑定 next TaskFlow。`CurrentCPU` 是线路创建
时独立绑定的 selector，不扫描 `OnCpu` Task，也不通过 current TaskFlow 解引用。

`Task.Action::Exit(status)` 是不返回的终端接口。Task 类型提供明确的默认未实现
终端；`KernelInitTask` override Linux 全局 init 保护并 panic：

```text
Attempted to kill init!
```

panic 前 Runtime、TaskFlow 与 Task 保持真实状态，不伪造销毁、Suspend 或 runq 变化。

临时模型映射：[model/objects/task.spec](../../model/objects/task.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
