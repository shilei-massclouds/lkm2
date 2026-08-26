# derive 有限 Relation/Map 逻辑里程碑计划

本文定义 modelc、Model IR 和 derive 增加有限二元关系查询能力的实施边界。model
表面提供对象拥有的 `Relation<K, V>`、`Map<K, V>`、成员查询和只读 witness
绑定；derive 内部统一把二者表示为类型化 tuple 集合。本里程碑以
`BootCommandLine -> EarlyConTable -> EarlyConsole` 为验收实例，证明 backend
可以由两次 lookup 决定，而不必在 `EarlyConsole` 模型中写死具体实现对象。

本文是路线和验收约束，不表示这些语法已经可用。本轮只落地计划文档，不新增正式
EarlyConsole model、coding 或 impl，也不改变当前 Model IR v12、derive result v11
及既有推导行为。

## 1. 目标与非目标

本里程碑需要完成以下能力：

- 用有限 `Relation<K, V>` 表示同一 key 可以关联多个 value 的单调事实集合。
- 用有限 `Map<K, V>` 表示满足 key 唯一约束的单调事实集合。
- 在条件中查询 `contains`、`has_key`，在 handler preflight 中通过
  `unique_value` 或 `lookup` 获得类型化 witness。
- 让 witness 作为 handler 内只读局部变量，复用现有条件、signal、字段更新和事实
  建立机制。
- 保证 tuple 建立、查询、轨迹和 JSON 输出不受建立顺序、哈希迭代顺序影响。
- 保持现有 predicate facts、`Collection<T>`、状态迁移、drives、emits 和事务边界
  兼容。

首版明确不包含：

- 通用 predicate join 或独立规则引擎；
- 递归规则、否定、`forall`、通用 `exists` 或集合推导；
- Relation/Map 的运行期遍历、长度、索引或顺序观察；
- tuple 删除、Map 覆盖以及 Relation/Map 对象的重新赋值；
- 依赖参数出现顺序的“第一个值”或“最后一个值”策略；
- 命令行字符串扫描、注册表遍历、函数指针安装等 coding/impl 算法。

有限在这里表示一次推导快照中只有有限个已建立 tuple；它不赋予 model 枚举这些
tuple 的能力。

## 2. Model 接口与数学语义

### 2.1 Relation

`Relation<K, V>` 对象在快照中的值是一个有限集合：

```text
R subseteq K x V
```

同一 key 可以对应零个、一个或多个 value。重复建立完全相同的 `(key, value)`
是幂等操作，不产生第二份成员，也不改变轨迹之外的逻辑结果。

首版提供三种操作：

- `contains(key, value) -> bool`：当且仅当 `(key, value)` 是成员时为真。
- `has_key(key) -> bool`：当且仅当至少存在一个 `(key, value)` 时为真。
- `unique_value(key) -> V`：恰好存在一个关联 value 时返回类型化 witness；零个
  value 时以 key 缺失失败，多个 value 时以歧义失败。

`unique_value` 不按建立顺序选值。只要候选不是恰好一个，它就必须失败。

### 2.2 Map

`Map<K, V>` 是附加函数性约束的 Relation：

```text
forall k, v1, v2:
    (k, v1) in M and (k, v2) in M implies v1 == v2
```

首版提供：

- `contains(key, value) -> bool`；
- `has_key(key) -> bool`；
- `lookup(key) -> V`：key 存在时返回唯一 value 的类型化 witness，不存在时失败。

向 Map 重复建立相同 tuple 是幂等操作；建立同 key、不同 value 的 tuple 是
`map_key_conflict`，并使所属 handler 的本次提交原子失败。Map 不提供覆盖语义，
也不根据建立先后决定胜者。

### 2.3 类型和值

Model 类型系统增加内建 `String` term，并支持二参数内建容器类型：

```text
Relation<K, V>
Map<K, V>
```

首版至少支持 `String` 和对象引用作为 K/V term；不引入嵌套 Relation/Map 值。
字符串按解码后的精确值比较，不做大小写折叠、Unicode 归一化或命令行语法解释。
对象引用按对象标识比较。

当容器参数是 model 对象类型时，沿用现有单继承赋值规则：声明为
`EarlyConBackendType` 的 value 可以接受 `SbiConsole` 这类具体派生实例，反向赋值
则不合法。modelc 必须在编译期拒绝 key/value 参数类型不匹配、错误泛型元数以及不受
支持的 term 类型；derive 不用把这些错误推迟到运行期。

## 3. 建立成员事实

Relation/Map 条目是由 `establishes` 增加的单调事实：

```text
establishes {
    BootCommandLine.contains("earlycon", "sbi");
    EarlyConTable.contains("sbi", SbiConsole);
}
```

同一个 `contains` 调用根据上下文有两种明确含义：

- 在 `depends_on`、`ensures` 或 invariant 等只读表达式中，它查询成员关系；
- 在 `establishes` 中，它把已类型检查的 tuple 加入目标对象。

除此之外的上下文不能把 `contains` 当成隐藏更新。`has_key`、`unique_value` 和
`lookup` 始终只读，不能出现在 `establishes` 中充当 effect。

tuple 与现有 predicate fact 共用 handler 的暂存和提交边界，但存储上保持显式类型
信息。一次 handler 提交中的目标状态、`updates`、predicate facts 和 tuple facts
要么按现有事务边界一起提交，要么都不提交；Map 冲突必须在允许该 handler 产生
状态变化或后续 emits 之前发现。同步 nested drive 已提交的子 handler 是否回滚继续
遵守现有 derive 事务语义，本里程碑不偷偷扩大跨 handler 回滚边界。

首版没有 tuple 删除语法。查询观察的是当前稳定快照加上按现有 handler 规则可见的
已提交事实，不能观察 Python 容器、文件顺序或实现内部迭代器。

## 4. 只读 witness 绑定

handler 增加 `binds` block：

```text
binds {
    value := BootCommandLine.unique_value("earlycon");
    backend := EarlyConTable.lookup(value);
}
```

绑定规则冻结如下：

1. 一个 handler 可以声明一个 `binds` block；绑定名不得与 handler 参数、隐式局部名
   或另一绑定重名。
2. 绑定按源码声明顺序求值。右侧可以引用 handler 参数和更早的绑定，不得后向引用
   尚未求值的绑定。
3. 所有绑定都是只读局部值，不能成为 `updates` 左侧，也不能被第二次定义。
4. 绑定名在所属 handler 的条件和 effect 中可见，可用于 `depends_on`、drives 参数、
   `updates` 右侧、`ensures`、`establishes` 和 `emits`。若既有 handler 项还允许其它
   普通 term 表达式，也沿用同一只读作用域；这不额外授权 continuation selector
   等目前禁止的类型组合。
5. modelc 根据所有 binding 声明建立 handler 局部作用域，因此源码中位于 `binds`
   之前、但语义上属于 preflight 的 `depends_on` 也可以引用绑定；binding 的右侧仍只
   能引用声明顺序中更早的 binding。未定义名、后向引用和重定义必须是编译错误。
6. 首版 binding 右侧只需要支持本计划定义的 witness-producing lookup；不借此引入
   任意赋值语句、可变局部变量或通用集合计算。

虽然绑定变量在表达式中表现为 `V`，轨迹中必须保留它是由哪一个类型化查询产生的，
而不能只留下格式化字符串。

## 5. Handler preflight 与原子性

derive 对含 binding 的 handler 使用以下确定顺序：

```text
handler/参数匹配
  -> 检查不依赖 witness 的 depends_on
  -> 按声明顺序执行 bindings
  -> 检查依赖 witness 的 depends_on
  -> 暂存并验证本 handler 的 updates/establishes（包括 Map key 约束）
  -> 进入既有 drives、ensures 和提交流程
  -> 提交状态、字段和事实，再按既有规则处理后续 effect
```

这里的“依赖 witness”由 Model IR 中解析完成的局部引用决定，不由文本块位置或运行期
猜测决定。先检查独立条件可以避免在明显不满足的 handler 中制造无意义 lookup
失败；绑定完成后再检查依赖条件，保证例如 `backend.state` 使用的是已经记录的
具体 witness。

preflight 中任一步失败时，本 handler 不得 drive、改变状态/字段、建立 predicate 或
tuple fact，也不得产生 emits。已经成功求得的早期 binding 仍保留在失败轨迹中，便于
解释后续条件为何失败。例如 backend 已绑定但不是 Online 时，轨迹必须显示该 backend，
而最终 `EarlyConsole.backend` 及状态保持原值。

## 6. derive 内部表示与确定性

derive 不为此功能增加第二套 predicate-join 引擎。Relation、Map 以及现有零元/带参
predicate fact 在求值层统一落到类型化 tuple 集合；Relation/Map 对象额外携带容器
种类与 K/V 类型，Map 在暂存和提交时执行 key 唯一检查。

推荐的逻辑记录至少包含：

```text
(owner object, container kind, key type, value type, key term, value term)
```

具体 Python 类可以不同，但以下性质是公共行为：

- tuple 相等使用类型化 term 相等，不依赖 renderer 文本；
- duplicate tuple 在集合层去重；
- Map 冲突针对提交前快照和同一事务内全部暂存 tuple 一并检查；
- 快照、轨迹和 JSON 使用规范排序，不能直接暴露 set/dict 遍历顺序；
- 改变启动 tuple 的建立顺序，不改变 binding、失败原因、轨迹规范 JSON 或最终状态；
- load/dump 后的类型信息足以重新执行相同查询和冲突检查。

`unique_value` 和 `lookup` 返回的 witness 记录至少需要包含绑定名、静态类型、规范化
值、查询表达式、owner 与 key。对象引用输出使用 Model IR 中的规范限定对象名；
String 输出使用 JSON 可逆字符串值。

## 7. 轨迹与诊断

Derivation Result 和人类 renderer 都要显示 binding 的求值，而完整 JSON 还要保留
结构化表达式、输入 key、类型化 witness 以及失败类别。至少区分：

| 场景 | 建议的机器失败类别 | 必须保留的上下文 |
| --- | --- | --- |
| Relation 中没有命令行 key | `relation_key_missing` | owner、key、binding、表达式 |
| Relation 同 key 有多个 value | `relation_key_ambiguous` | owner、key、规范排序的候选值 |
| Map 中没有注册名 | `map_key_missing` | owner、key、binding、表达式 |
| Map 同 key 建立不同 value | `map_key_conflict` | owner、key、既有值、待建立值 |
| 已绑定 backend 状态不满足 | 既有 `depends_on` 失败类别 | 已成功 binding 和失败条件 |

名称可以在实现评审时与现有错误枚举对齐，但五类结果不能合并成一个无上下文的
`lookup_failed`。modelc 的静态诊断则要分别说明容器元数、key/value 类型、binding
未定义、后向引用和重定义问题，并指向对应源码范围。

引入结构化 binding/tuple 输出后，Model IR 和 Derivation Result 都需要提升 schema
版本并更新严格 loader、dumper 和 golden。按当前基线，若中间没有其它 schema 变更，
预期分别从 Model IR v12 升至 v13、从 result v11 升至 v12；sequence v3 不因本计划
自动升级。若实现前已有其它 schema 演进，则使用当时的下一个版本，不能复用旧版本号
承载新字段。

## 8. EarlyConsole 验收模型

下面的独立 tool fixture 是语义验收目标，不是本轮要加入 `model/` 的正式模型：

```text
object BootCommandLine: Relation<String, String> {
    initial_state: State::Ready;
}

object EarlyConTable: Map<String, EarlyConBackendType> {
    initial_state: State::Ready;
}

object SbiConsole: EarlyConBackendType {
    initial_state: State::Online;
}

object EarlyConsole: EarlyConsoleType {
    initial_state: State::Ready;

    attrs {
        mutable backend: EarlyConBackendType;
    }

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
                depends_on {
                    BootCommandLine.state == State::Ready;
                    EarlyConTable.state == State::Ready;
                }

                binds {
                    value := BootCommandLine.unique_value("earlycon");
                    backend := EarlyConTable.lookup(value);
                }

                depends_on {
                    backend.state == State::Online;
                }

                updates {
                    self.backend = backend;
                }

                establishes {
                    early_console_bound_from_registry(self, backend);
                }
            }
        }
    }
}
```

fixture 在 Enable 前通过显式 setup 建立：

```text
BootCommandLine.contains("earlycon", "sbi");
EarlyConTable.contains("sbi", SbiConsole);
```

成功路径必须得到：

```text
value   := "sbi"
backend := SbiConsole
```

随后 `EarlyConsole.backend` 更新为 `SbiConsole`，状态进入 `State::Online`，并建立
`early_console_bound_from_registry(EarlyConsole, SbiConsole)`。模型中不存在静态
`EarlyConsole -> SbiConsole` 绑定；选择结果只由两个有限关系的内容决定。

fixture 的 setup 必须通过公开的初始快照/handler 事实建立路径完成，不能直接修改
derive 的私有 Python store。正式采用哪一种 setup 表面在对应实现里与现有测试夹具
统一，但两条成员事实都必须进入可重放输入或因果轨迹。

## 9. Model、coding 与 impl 分工

| 层次 | 本例负责内容 | 不负责内容 |
| --- | --- | --- |
| model | 声明有限关系、唯一性、lookup 依赖、backend 状态前置条件和绑定后的状态/事实 | 字符扫描、表循环、地址或函数指针安装 |
| coding | 描述命令行解析、backend 注册表查找、从逻辑对象到可调用 backend 的绑定算法 | 固定平台实现细节和具体寄存器操作 |
| impl | 实现 Linux sibling/本内核中的具体解析、表布局、SBI console 调用和错误路径 | 改写 model 的 Relation/Map 逻辑语义 |

lookup 是对稳定关系的逻辑观察，不表示 model 正在描述字符串扫描或表遍历。工具能力
稳定后，正式 EarlyConsole/SbiConsole 必须另起 model -> coding -> impl 评审；不得以
本 fixture 直接替代正式模型与章程。

## 10. 实施里程碑

### 里程碑 1：文档与边界冻结

- 评审本文的 Relation/Map 数学语义、单调性、类型边界和失败规则。
- 冻结 model/coding/impl 分工以及 EarlyConsole 只作 tool fixture 的范围。
- 冻结确定性要求、事务可见性和 schema 升级策略。

完成标准：实现者无需自行决定同 key 多值、Map 冲突、lookup 缺失或 tuple 顺序的
语义。

### 里程碑 2：Model IR 与 modelc

- 扩展 grammar/AST，加入 `binds`、`:=` 和对应源码范围。
- 增加双参数 `Relation`/`Map`、内建 `String` term 及容器方法签名。
- 在名字解析后建立 handler 局部 binding 符号表，完成顺序依赖、只读和类型检查。
- 在 Model IR 中表达容器种类、K/V 类型、binding 列表和表达式对 binding 的引用。
- 提升 Model IR schema，严格更新 JSON load/dump 和 canonical golden。

完成标准：合法 fixture 可生成可重复 IR；类型错误、未定义 binding、后向引用和重定义
都由 modelc 拒绝。

### 里程碑 3：derive 关系存储

- 增加类型化 tuple store，并把 Relation/Map owner 纳入运行时快照。
- 实现 `establishes Owner.contains(k, v)` 的暂存、幂等去重和提交。
- 对 Map 执行快照内及同事务内的 key 唯一检查，冲突时原子失败。
- 为快照和 JSON 定义规范排序；不另建 predicate-join 引擎。

完成标准：Relation 同 key 多值合法；Map 相同 tuple 重复建立幂等；Map 同 key 不同值
失败且本 handler 无部分提交。

### 里程碑 4：查询与 witness

- 实现 `contains`、`has_key`、`unique_value` 和 `lookup`。
- 实现 preflight 的独立条件、顺序 binding、依赖条件三阶段求值。
- 把类型化 witness 注入现有表达式、signal 参数、updates 和 facts 机制。
- 保证 binding 或依赖条件失败发生在 drive/state change 之前。

完成标准：EarlyConsole 成功路径通过，四种 lookup/状态失败路径具有正确快照和回滚
结果。

### 里程碑 5：轨迹与诊断

- Derivation Result 增加 binding 求值和 tuple 快照，提升 result schema。
- 完整 JSON 保存表达式、静态类型、值和失败原因；终端输出展示简洁因果绑定。
- 为缺失、歧义、注册缺失、Map 冲突和 backend 状态失败提供互不混淆的诊断。
- 更新严格 JSON loader/dumper、repeatable-output 测试与全部 result golden。

完成标准：失败轨迹足以回答“查了哪个 key、在哪个对象失败、已绑定到什么对象”，
且 dump/load 后信息不丢失。

### 里程碑 6：Earlycon fixture

- 在独立 tool 测试目录加入最小 EarlyConsole 类型、对象和 setup。
- 覆盖成功、命令行缺值/多值、未注册、Map 冲突和 backend 非 Online。
- 用不同 tuple 建立顺序运行同一 fixture，比较规范轨迹和最终状态。
- 不把 fixture 提升为 `model/` 正式模型，不新增 coding/impl。

完成标准：第 11 节验收矩阵全部自动化，且现有模型不需要 Relation/Map 即可保持原
行为。

### 里程碑 7：后续交接

- 汇总工具语义、schema 变化、诊断和 fixture 证据。
- 独立发起正式 EarlyConsole/SbiConsole 的 model -> coding -> impl 评审。
- 在正式评审中决定真实命令行解析顺序、backend 表布局和函数绑定实现，而不是回填到
  derive 逻辑中。

## 11. 测试与验收矩阵

### 查询和集合语义

- `contains` 对存在/不存在 tuple 分别返回 true/false。
- `has_key` 对有/无任意关联 value 分别返回 true/false。
- Relation 同 key 多 value 可以提交，`unique_value` 报告歧义并规范列出候选。
- Map 同 key 同 value 重复建立幂等，同 key 不同 value 原子失败。
- String 和对象引用均按类型化值比较，派生 backend 实例可用于基类型 value。

### EarlyConsole 路径

- `earlycon=sbi` 选择 Online 的 `SbiConsole`，写入 backend、进入 Online 并建立绑定
  predicate fact。
- 缺少 `earlycon` 时在第一个 binding 报告命令行 key 缺失。
- `earlycon` 有多个 value 时在第一个 binding 报告歧义，不任选一个。
- `earlycon` value 未注册时，第一个 binding 成功，第二个 binding 报告 backend name
  缺失。
- backend 非 Online 时两个 witness 都保留在失败轨迹中，但 EarlyConsole 状态、字段和
  新事实不提交。
- Map 注册同名不同 backend 时 setup/所属 handler 以 Map 冲突失败，不能留下部分
  registry。

### 静态检查与兼容

- Relation/Map key/value 类型不匹配、错误参数数量和不支持的 term 类型由 modelc
  拒绝。
- 未定义 binding、binding 后向引用、与参数重名和 binding 重定义由 modelc 拒绝。
- binding 用作 `updates` 左侧或其它可变目标时由 modelc 拒绝。
- 改变 tuple 建立顺序不改变 witness、规范轨迹和最终状态。
- 更新 Model IR/derive schema golden，并验证旧 schema 被严格拒绝而非错误解释。
- 现有 predicate facts、`Collection<T>`、状态迁移、drives/emits/resumes 和当前模型
  golden 保持兼容。

每个实施里程碑都运行：

```sh
make derive
make test
make difftest
git diff --check
```

若某一里程碑按仓库约定尚不应运行环境昂贵的 `difftest`，提交证据必须明确记录未运行
原因，并在能力合入前完成；不能用新增 fixture 通过替代现有回归集合。

## 12. 固定假设与开放实现细节

以下假设在本路线中固定：

- `BootCommandLine` 使用 Relation，因为真实命令行可能重复出现同一 key；首版不冻结
  重复参数的解析顺序或覆盖策略。
- `EarlyConTable` 使用 Map，因为 backend 注册名必须唯一。
- 所有 Relation/Map 条目都是单调事实，首版没有删除、覆盖和运行期枚举。
- backend 的选择由关系内容决定；`EarlyConsole` 不静态引用 `SbiConsole`。
- 命令行解析、backend 表遍历和函数绑定算法属于 coding，具体平台实现属于 impl。

以下内容可以在不改变上述语义的前提下于实现评审中确定：

- Model IR/Python 数据类的具体字段名；
- binding 和 tuple trace 在 JSON 中的具体嵌套布局；
- fixture 使用公开初始快照还是显式 setup handler 建立启动 tuple；
- 机器失败类别与现有诊断枚举对齐后的最终拼写；
- canonical tuple 的内部排序 key，只要输出稳定且不改变类型化相等语义。
