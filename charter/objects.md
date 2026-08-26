# Objects 章程

## 定位

`objects` 表达内核执行期间可寻址、具有独立生命周期的运行对象。对象类型可以定义共同协议，但具体运行状态必须属于明确的对象实例，不能因为推导器使用中央事件循环而退化成隐含的全局内核单例。

当前模型只覆盖 BootCPU/逻辑 CPU0 所需的最小对象切片。CPU 已具有明确对象身份和
逻辑编号；后续增加拓扑时必须保持每 CPU 对象的独立身份和状态。

## CPU 推导线路

公开推导执行必须先建立一条 CPU 推导线路。当前单 CPU 阶段中，一条有效线路恰有一个
逻辑 CPU、一个 CPU-owned `sched_core` Scheduler，以及推导器 owned 的
`CurrentCPU` 与 `CurrentTaskRef` selector。线路创建时立即从 Scheduler owner 绑定
`CurrentCPU`；`CurrentTaskRef` 初始 unavailable，直到 BootTask 在 OnCpu 状态成功执行
bootstrap `ResetCurrent`。缺少 Scheduler、存在多个 Scheduler或缺少 CPU owner 都是
线路构造失败；尚未初始化 current Task 本身是合法线路状态。

`CurrentTaskRef` 是 model 可读、推导器维护的动态 Task selector。任意 handler 均可
把它作为 signal target 或参数读取，但 model 不得声明、赋值、更新或以其他方式改变
它。每条候选推导路径独立维护 current；推导器只在完整调度切换成功后原子提交。

model 负责声明对象生命周期、信号因果和何时发生调度切换；推导器负责
CPU 线路身份与 current、per-CPU interrupt control、Scheduler 的隐藏 runnable 集合、
全候选分支展开、切换提交以及
Task-owned 恢复 selector 的动态解析。model 不声明调度优先级或候选队列；
成功 Resume 后是否进入恢复点由 model handler 显式声明。两层职责不得互相替代。

未来 SMP 必须为每个 CPU 建立独立线路及 CPU-owned Scheduler。禁止在同一线路维护多个
current，也禁止多个 CPU 共享一个 Scheduler runtime。

## 当前映射

- [Printk、Console 与 Banner 章程](objects/printk.md)
- [Scheduler 章程](objects/scheduler.md)
- [CPU 与 EventFlow 章程](objects/cpu.md)
- [Task 与 BootTask 章程](objects/task.md)
- [UserAppRuntime 章程](objects/user_app_runtime.md)
- 临时模型映射：[model/objects.spec](../model/objects.spec)

章程与模型的关系见[系统章程](main.md)。
