# UserAppRuntime 章程

## 定位与 ownership

`UserAppRuntime` 是承载用户态执行坐标的独立运行对象，不是 Task、TaskFlow 或
Scheduler 的组成部分。当前唯一实例 `KernelInitUserAppRuntime` 由
`KernelInitFlow` owned；它是被动 child，只在其 owner 的 `UserRunPhase` 明确驱动时
推进生命周期，不参与调度队列，也不改变 Scheduler 的 current Task。

本阶段不为 Runtime 声明 `ApplicationInstance`。具体应用、exec 与地址空间等语义在
开始模拟应用执行时另行引入。

## 生命周期与用户入口

Runtime 的最小生命周期是：

```text
Base --Preset--> Prepared --Setup--> Ready --Enable--> Online
Online --Enter--> 用户态执行断点
```

`UserRunPhase.Action::Enter` 必须同步依次驱动 `Preset`、`Setup`、`Enable`，然后
`yields KernelInitUserAppRuntime.Action::Enter`。`Enable` 只发布 Online 状态；只有
`Action::Enter` 表示控制到达用户态执行坐标。

该 yield 是推导器持有的用户态断点，不沿 `UserRunPhase` 的 Enter 调用点返回。
断点建立后，`KernelInitTask` 仍是 CPU0 current Task 并保持 `State::OnCpu`，Runtime
保持 `State::Online`，Scheduler runq 不因进入用户态而变化。

## trap 与 exit 边界

未来的 syscall、异常和中断从该用户态坐标进入对应 trap 入口，而不是从 yield 后方
恢复 `UserRunPhase`。普通 syscall 可以在处理完成后返回同一用户态坐标；exit 将终止
Runtime，并由异常或中断入口进入 KernelInitFlow/KernelInitTask 回收链。

当前模型不实现用户指令、syscall、exit、Runtime Disable/Cleanup 或 Task 回收。

临时模型映射：[model/objects/user_app_runtime.spec](../../model/objects/user_app_runtime.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
