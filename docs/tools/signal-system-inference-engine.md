# 信号与系统推导引擎设计备忘录

本文记录关于一种事件驱动推导引擎的讨论结论。

## 1. 基本模型

模型只包含两种核心要素：

```text
Signal:
  系统之间传递的信息和控制请求

System:
  拥有若干状态
  默认静止在当前状态
  只有收到信号时才可能发生状态迁移
```

一个系统可以描述为：

```text
System:
  name
  initial_state
  current_state
  local_data
  transitions
```

一条迁移规则可以描述为：

```text
Transition:
  source_state
  accepted_signal
  guard
  target_state
  actions
  emitted_signals
```

处理过程：

```text
1. 系统收到一个信号
2. 按当前状态和信号类型查找迁移规则
3. 检查 guard
4. 条件成立时，原子地提交状态和局部数据变化
5. 将迁移产生的新信号投递出去
6. 没有匹配规则或条件不成立时，系统保持原状态
```

“处理一个信号并提交一次完整迁移”是建议的最小原子步骤。不要在迁移执行一半时切换任务。

## 2. 信号的两个独立维度

同步或异步是一种投递语义；发送后是否继续运行是另一种语义。两者不能混成一个字段。

```text
Signal:
  kind
  source
  target
  delivery
  sender_effect
  payload
  correlation_id
```

投递方式：

```text
delivery = async:
  发送者不等待普通应答

delivery = request_reply:
  请求和应答使用相同 correlation_id
  发送者等待对应应答
```

发送者效果：

```text
sender_effect = continue:
  发送后继续运行

sender_effect = wait_reply:
  暂停并等待同步应答

sender_effect = surrender:
  立即交还运行权

sender_effect = block:
  交还运行权并进入阻塞状态

sender_effect = exit:
  交还运行权并结束
```

典型信号：

```text
普通异步通知:
  kind = NOTIFY
  delivery = async
  sender_effect = continue

同步请求:
  kind = CALL
  delivery = request_reply
  sender_effect = wait_reply

主动让出:
  kind = YIELD
  delivery = async
  sender_effect = surrender

阻塞:
  kind = BLOCK
  delivery = async
  sender_effect = block

调度派发:
  kind = DISPATCH
  delivery = async
  payload = run_token
```

同步调用在实现上不应使用互相嵌套的 Python 同步函数调用。应表示成“请求信号 + 暂停位置 + 应答信号”，以避免调用环和递归死锁。

## 3. Policy 与 Scheduler 的职责

最终讨论结论：

```text
Policy:
  判断是否需要结束当前运行区间

Scheduler:
  维护 runnable queue
  在被调用后选择任务
  执行运行权交接
  产生新 run_token
  发出 DISPATCH
```

如果 Policy 判断不需要重新调度：

```text
不调用 Scheduler
当前任务继续运行
当前 run_token 保持有效
```

一旦调用 Scheduler：

```text
旧 run_token 必然失效
Scheduler 必然作出一次新派发
不存在“进入 Scheduler 但保持旧 token”的路径
```

Scheduler 的统一接口可以是：

```text
Scheduler.schedule(
  current_token,
  current_disposition
)
```

`current_disposition` 表示如何处理旧任务：

```text
requeue:
  任务仍可运行，重新放入 runnable queue

block:
  任务正在等待，不进入 runnable queue

exit:
  任务已经结束
```

Scheduler 可能选择：

```text
其他任务:
  实际发生任务切换

原任务:
  没有其他候选，原任务开始新的运行区间

idle:
  没有任何可运行任务
```

无论最终是否还是原任务，进入 Scheduler 后都产生新 token。

## 4. run_token 的语义

`run_token` 不是任务的永久身份，而是一次性的 CPU 运行许可证。

```text
task_id:
  表示任务身份
  创建后保持不变

run_token:
  表示某个任务在某个 CPU 上的一段连续运行区间
  每次派发产生一个新值
  只能用于这一段运行
```

一个有效 token 绑定：

```text
cpu_id
task_id
run_interval
```

单核示例：

```text
scheduler:
  current_task = task_1
  current_token = 41

scheduler -> task_1:
  DISPATCH(token=41)

task_1 -> scheduler:
  YIELD(token=41)
```

task 发出带有交还运行权语义的信号后：

```text
task 侧:
  token=41 立即进入 SURRENDERED
  task 不得再执行或再次使用 41

scheduler 侧:
  收到请求后验证 owner 和 token
  验证成功后将 41 标记为 CONSUMED
```

信号仍在队列中时，Scheduler 保留 token 的权威记录只是为了验证请求；任务已经不能继续执行。

若 Scheduler 最终仍选择 task_1：

```text
token=41 失效
产生 token=42
DISPATCH(task_1, token=42)
```

token 可以只是单调递增整数，不需要随机数或昂贵的对象分配。

一次性 token 的作用：

```text
保证每个 CPU 同时只有一个任务拥有运行权
拒绝延迟、重复和乱序的旧信号
明确划分连续运行区间
支持确定性的执行记录和重放
```

## 5. Scheduler 派发顺序

Scheduler 不直接修改目标任务内部状态。系统之间仍通过信号交互。

推荐过程：

```text
Scheduler 的原子迁移:
  1. 使旧 token 失效
  2. 选择目标任务
  3. 产生新 token
  4. 在自身状态中记录 token 与目标任务的授权绑定
  5. 将 DISPATCH(target, token) 加入信号队列

目标任务收到 DISPATCH:
  1. 验证自己允许被调度
  2. 接受 token
  3. 进入运行状态
```

如果内部信号保证可靠，而且“保存授权 + DISPATCH 入队”是同一个原子迁移，可以不使用 ACK。

如果信号可能丢失，或需要模拟分布式系统，可以增加：

```text
dispatch_pending
DISPATCH_ACK
timeout
```

## 6. 单核调度过程

主动让出的完整过程：

```text
初始:
  task_1 持有 token=41
  task_2 位于 runnable queue

task_1:
  发出 YIELD(token=41)
  立即停止

Scheduler:
  验证并消费 token=41
  将 task_1 放回 runnable queue
  选择 task_2
  产生 token=42
  发出 DISPATCH(task_2, token=42)

task_2:
  收到 DISPATCH
  接受 token=42
  开始新的运行区间
```

阻塞与唤醒：

```text
task_1 -> Scheduler:
  BLOCK(token=41, wait_id=100)

Scheduler:
  消费 token=41
  不将 task_1 放回 runnable queue
  派发其他任务或 idle

设备或其他系统 -> Scheduler:
  WAKE(task_1, wait_id=100)

Scheduler:
  验证 wait_id
  将 task_1 放入 runnable queue
```

`wait_id` 用于拒绝过期唤醒。

## 7. SMP 扩展

SMP 下，每个逻辑 CPU 各有一个有效 run_token。

```text
cpu_0:
  current_task = task_1
  token = 101

cpu_1:
  current_task = task_2
  token = 205
```

关键不变量：

```text
每个 CPU 最多有一个有效 token
每个 task 最多在一个 CPU 上持有 token
同一进程的不同线程作为不同 task，可以运行在不同 CPU
```

某个 CPU 重新调度只使该 CPU 的 token 失效：

```text
SCHEDULE(cpu=0, current_token=101, disposition=requeue)

Scheduler:
  consume token=101
  选择 task_3
  create token=102
  DISPATCH(cpu=0, task=task_3, token=102)
```

cpu_1 上的 token=205 不受影响。

第一版 SMP 建议使用：

```text
每个逻辑 CPU 一个独立 Scheduler 对象
多个逻辑 CPU
一个中央、确定性的事件循环
顺序一致的共享状态
可选的完整信号/响应序列枚举
```

这里中央化的是推导器的确定性事件循环，不是内核模型中的 Scheduler 实例。事件循环可以按确定顺序串行执行多个 per-CPU Scheduler 对象；每个对象仍保有独立身份、状态和 run token。局部 runqueue 与共享负载均衡系统可以后续补充，但不应先用一个全局 Scheduler 单例代替 per-CPU ownership。

## 8. 事件前沿，而不是逐轮扫描 CPU

引擎维护：

```text
pending_signals:
  所有尚未消费的信号

enabled_events:
  当前目标系统已经具备处理条件的信号
```

例如：

```text
signal_A -> 正在 cpu_0 运行的 task_1
  enabled

signal_B -> 尚未获得运行权的 task_2
  保留在 task_2 mailbox，不 enabled

signal_C -> Scheduler
  enabled
```

单条确定路径的主循环：

```text
while 路径中还有下一个确定事件:
  读取路径指定的 next_event
  验证 next_event 当前属于 enabled_events
  执行目标系统的一次原子迁移
  提交状态变化
  投递新产生的信号
  增量更新受影响的 enabled_events
```

单路径推导器不从多个 `enabled_events` 中随机或隐式选择一个。下一事件必须由正在验证的确定信号序列指定。需要搜索其它可能行为时，由独立的序列枚举器为每个合法的下一事件生成不同的确定序列，再分别调用同一个单路径推导器。

由于系统只有收到信号才可能迁移，某个 CPU 不会无缘无故变得可执行。因此不必每轮遍历全部 CPU：

```text
DISPATCH:
  为目标 CPU 注册可执行事件

任务 BLOCK:
  删除该任务的可执行事件

信号到达运行中的任务:
  将对应事件加入 enabled_events

信号到达未运行任务:
  只进入该任务 mailbox
```

为了调试，可以偶尔全量扫描 CPU 并重新计算 `enabled_events`，验证增量索引正确；这不是正常执行路径。

## 9. 确定性路径推导与序列穷尽

目标系统可以具有多个 CPU、多个任务以及并行或并发行为，但一次推导本身不是随机或并行的。一次推导的输入是一条全序、确定的信号/响应序列；推导器严格按照该序列逐项执行，因此每条路径只有一条轨迹和一个结果。

```text
DerivationPath:
  initial_snapshot
  ordered_signal_and_response_sequence
```

序列中的每一步都应记录足以唯一确定事件的内容，包括 Signal identity、source、target、payload、投递次序和必要的 token/correlation 信息。推导器在执行每一步前验证对应事件确实已经产生且当前可执行；序列指定了不存在、尚未产生或当前不可执行的事件时，该路径在确定的位置失败。

并发行为表现为可能存在多条不同的确定序列，而不是单次推导内部存在不确定选择。例如当前同时存在两个合法事件时：

```text
sequence_1 = [..., step_A, step_B, ...]
sequence_2 = [..., step_B, step_A, ...]
```

`sequence_1` 和 `sequence_2` 是两个独立的验证对象。推导器分别执行并验证它们，不在一次执行中随机选择分支，也不把两条序列解释为同一推导中的两种中间轨迹。

在状态空间允许时，序列枚举器应穷尽生成所有可能的确定信号/响应序列，并逐条交给推导器验证。穷尽搜索是对确定路径集合的枚举，不改变任何单条路径的确定性。不同序列可以由多个搜索 worker 并行处理，但并行只属于实现层的吞吐优化，不进入推导语义。

当状态空间过大而无法穷尽时，工具至少必须支持：

```text
针对指定序列完成确定性推导
完整记录该序列及其执行轨迹
从相同初始快照精确重放
明确报告搜索覆盖范围和未覆盖部分
不得把达到深度、路径数量或时间预算误报为穷尽完成
```

状态哈希、前缀缓存和公共后缀复用可以减少重复计算，但必须保留每条确定序列的独立身份、来源和验证结果，不能因为多个序列到达相同状态就丢失路径覆盖记录。

## 10. 推导成功与确定性条件

规格推导通过的基本条件是：在外部信号停止产生后，指定的确定信号/响应序列最终归于静止，并且相同序列可以得到唯一且可重放的结果。该条件分为单路径成功和搜索覆盖两个层次。

### 10.1 最终静止

当外部不再产生新信号时，当前确定序列必须完整处理系统内部已经存在的信号及其后续产生的内部信号，并在有限次迁移后停留在一个确定状态上：

```text
外部信号形成一个有限序列
内部迁移和内部信号传播在有限步内结束
enabled_events 为空
pending_signals 为空
不存在未来 timer、待恢复 continuation 或尚未完成的 request/reply
系统完整状态不再发生变化
```

暂时没有可执行事件不等于成功静止。出现以下任一情况时，均不能判定推导通过：

```text
内部信号无限产生或状态无限迁移
仍有 pending_signals，但不存在能够处理它们的 enabled_event
仍有可能解除等待的未来事件，但推导提前停止
推导因深度、路径数量或时间预算耗尽而被截断
```

其中，存在未处理信号却无法继续推进属于死锁或永久阻塞；预算耗尽只能得到“尚未确定”的结果，不能当作已经收敛。

### 10.2 单路径结果确定性

当系统初始状态和完整有序的信号/响应序列固定时，每次完整推导都必须得到相同的执行轨迹和系统结果状态：

```text
相同 initial_snapshot
+ 相同 ordered_signal_and_response_sequence
=> 相同 transition_trace
+ 相同 final_system_state
```

`final_system_state` 是完整的规范化状态快照，至少包括所有 System 的状态和局部数据、Scheduler/CPU/Task 状态、token 与等待关系，以及最终信号和 timer 状态。比较结果状态时不依赖对象地址、哈希随机种子或实际线程调度等实现细节。

目标系统的并发性不能给单路径推导引入额外随机性。两个不同的有序信号序列是两条不同路径，不属于“相同输入”，不在单路径确定性条件中比较它们的中间轨迹或最终状态。

因此，单条路径推导成功可以概括为：

```text
对于固定初始快照 S0 和确定信号/响应序列 Q：
  1. derive(S0, Q) 在有限步内到达静止状态 F
  2. replay(S0, Q) 产生相同轨迹并满足 normalize(F') == normalize(F)
```

### 10.3 穷尽搜索与逐路径验证

对某个搜索范围宣称“规格验证通过”，必须已经枚举该范围内所有可能的确定信号/响应序列，并且每条序列都独立完成推导和验证。若搜索无法穷尽，则不能给出该范围整体通过的结论，但已经完成的特定序列仍具有有效的逐路径结果、完整记录和可重放性。

```text
PathPassed:
  一条指定序列完成推导并通过该路径的全部检查

SearchPassed:
  搜索范围已经穷尽，并且其中每条序列都是 PathPassed

SearchIncomplete:
  搜索范围尚未穷尽；保留逐路径结果，但不声称整体通过
```

## 11. 锁、抢占与逻辑时间

锁可以作为一个独立系统：

```text
ACQUIRE:
  锁空闲时返回 ACQUIRED
  锁占用时将任务加入等待队列

RELEASE:
  释放锁
  选择等待任务并发出 ACQUIRED
```

跨 CPU 抢占：

```text
事件唤醒高优先级任务
Policy 判断需要结束某个 CPU 的当前运行区间
目标 CPU 在下一个原子边界进入 Scheduler
旧 token 失效
Scheduler 作出新派发
```

不要在一次状态迁移执行到一半时撤销 token。

逻辑时间：

```text
当前 enabled_events 为空，但存在未来 timer:
  推进到最近的 timer 时间

pending_signals 和未来 timer 都为空:
  系统完全静止

pending_signals 不为空、没有 enabled_events、也没有可解除等待的未来事件:
  可能发生死锁
```

## 12. 当前建议的最小实现

```text
SignalEnvelope
SystemSpec
SystemInstance
TransitionResult
EventQueue
EnabledEventIndex
SchedulingPolicy
Scheduler
CpuSlot
TaskControlBlock
RunTokenAllocator
TraceRecorder
StateHasher
StateNormalizer
QuiescenceDetector
SignalSequenceEnumerator
PathDeriver
ReplayEngine
SearchCoverageTracker
```

第一阶段只实现：

```text
可靠的进程内信号
单线程事件循环
单核确定性执行
BootCPU 的独立 Scheduler 对象
同步请求/应答
YIELD、BLOCK、WAKE、DISPATCH
一次性 run_token
完整执行轨迹
指定信号/响应序列的严格执行
路径记录和精确重放
静止、死锁和截断结果的明确区分
相同序列的轨迹和结果状态可重复验证
```

第二阶段再增加：

```text
多个逻辑 CPU
可能信号/响应序列的枚举
共享资源和锁
状态计算缓存且保留路径身份
逐条路径验证和死锁检测
搜索覆盖范围及穷尽状态报告
```

更晚再考虑：

```text
弱内存模型
完整的 per-CPU runqueue 与共享负载均衡
并行执行彼此独立的确定序列搜索 worker
不可靠或分布式信号
持久化和故障恢复
```
