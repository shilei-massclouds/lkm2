# Objects 章程

## 定位

`objects` 表达内核执行期间可寻址、具有独立生命周期的运行对象。对象类型可以定义共同协议，但具体运行状态必须属于明确的对象实例，不能因为推导器使用中央事件循环而退化成隐含的全局内核单例。

当前模型只覆盖 BootCPU/逻辑 CPU0 所需的最小对象切片。后续增加 CPU 与拓扑模型时，必须保持每 CPU 对象的独立身份和状态。

## 当前映射

- [Scheduler 章程](objects/scheduler.md)
- [UserAppRuntime 章程](objects/user_app_runtime.md)
- 临时模型映射：[model/objects.spec](../model/objects.spec)

章程与模型的关系见[系统章程](main.md)。
