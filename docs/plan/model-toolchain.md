# 模型工具链总体计划

本文定义模型规格从编译、推导到未来代码生成的总体路线，作为后续 `modelc`、`derive` 和 `rustgen` 详细设计与实施计划的共同边界。

推导过程中的信号投递、状态迁移、调度、确定性与穷尽搜索语义以[《信号与系统推导引擎设计备忘录》](../tools/signal-system-inference-engine.md)为准。本文不重复这些语义细节，只规定各工具如何共享模型及如何分阶段落地。

## 1. 目标与范围

首期工具链分为编译和推导两段：

```text
.spec ──modelc──> Model IR ──derive──> 推导轨迹与最终状态
```

未来在同一份 Model IR 上增加 Rust 代码生成：

```text
                       ┌──derive──> 推导轨迹与最终状态
.spec ──modelc──> Model IR
                       └──rustgen──> Rust
```

这一划分有三个目的：

- `.spec` 的解析、名字解析和合法性判断集中在 `modelc`，下游无需各自理解源语言。
- `derive` 和未来的 `rustgen` 消费相同的、与源文件语法解耦的 Model IR。
- Model IR 既能在同一进程内高效传递，也能用稳定 JSON 保存、检查和跨工具交换。

本计划不提前锁定 `.spec` 的完整语法、Model IR 的全部字段、诊断格式或代码生成策略。这些内容应在对应阶段的详细计划中确定。

## 2. 组件与目录边界

计划采用以下目录职责，不增加 `lkm/` Python 包装层：

```text
tools/
├── bin/
│   ├── modelc              # modelc 命令行壳
│   └── derive              # derive 命令行壳
└── src/
    ├── modelc/
    │   ├── grammar.lark    # .spec 的独立 Lark grammar
    │   └── ...             # AST、解析、解析后检查与 IR 生成
    ├── model_ir/
    │   └── ...             # 共享 Model IR 类型及 JSON I/O
    └── derive/
        └── ...             # 推导状态、执行器、轨迹与搜索
```

`tools/bin/modelc` 和 `tools/bin/derive` 只负责命令行参数、输入输出选择、退出码以及面向终端的诊断呈现。可测试、可复用的核心功能分别位于 `tools/src/modelc/` 和 `tools/src/derive/`；两者共同依赖 `tools/src/model_ir/`。

未来引入 `rustgen` 时遵循相同原则：命令行壳放在 `tools/bin/rustgen`，核心实现放在 `tools/src/rustgen/`，只消费公共 Model IR，不重新解析 `.spec`。

## 3. modelc 编译管线

`modelc` 使用独立的 `tools/src/modelc/grammar.lark`，通过 Lark 解析 `.spec`。编译管线的职责顺序为：

```text
源文件装载
  → Lark 语法解析
  → AST 构造
  → 名字解析
  → 类型检查与语义检查
  → Model IR 生成
```

阶段之间应保持清晰边界，使语法错误、未解析名字、类型错误和语义错误可以归属到准确阶段。`include`、`use`、入口规格和跨文件引用等源语言行为最终由 modelc 详细计划定义；下游工具只观察解析完成后的 Model IR。

Model IR 应表达推导和未来代码生成共同需要的语义，而不是保存 Lark parse tree 或照搬源文件表面语法。首期 schema 只需覆盖当前 `model/**/*.spec` 及首版推导器所需信息，并保留版本演进空间。

## 4. 公共库接口与数据边界

公共库预期至少提供以下稳定入口：

```python
compile_spec(...) -> ModelIR
load_model_ir(...) -> ModelIR
dump_model_ir(...) -> JSON-compatible output
derive(model_ir, initial_snapshot, signal_sequence, ...) -> DerivationResult
```

这里规定的是职责稳定性，不是最终 Python 参数列表；具体类型、错误模型和可选项在各组件详细计划中确定。

两种数据边界各有明确用途：

- 内存中的 `ModelIR` 对象是 Python 公共库之间的接口。`modelc`、`derive` 和未来的 `rustgen` 在同一进程内直接传递该对象。
- Model IR JSON 是工具之间的稳定交换格式和调试格式，用于保存编译结果、复现问题、测试兼容性以及供其他进程消费。

因此，`derive` 的输入路径必须遵守以下规则：

```text
输入 .spec → 在当前进程调用 compile_spec() → ModelIR → derive()
输入 .json → 直接调用 load_model_ir()       → ModelIR → derive()
```

`.spec` 路径不得通过启动 `modelc` 子进程实现，也不得用临时 JSON 作为两个 Python 库之间的中转。这样可以保留结构化诊断，避免额外 I/O，并保证命令行工具和库调用共享同一套实现。

JSON schema 需要显式版本，并为相同 Model IR 提供规范、可重复的序列化结果。兼容性和迁移策略在 Model IR 详细设计中确定。

Pydantic 是 Model IR 层的候选实现，可用于：

- 从 JSON 反序列化 Model IR；
- 在公共边界执行运行时结构验证；
- 将 Model IR 序列化为 JSON。

是否正式采用 Pydantic、采用哪个版本以及哪些内部结构需要验证，留到 modelc 详细计划中决定；总体架构不依赖这一选择。

## 5. derive 的推导与搜索原则

`derive` 首先实现指定信号序列的单路径推导。其核心可重放性约束是：

```text
固定 Model IR + 固定初始快照 + 固定且确定的信号序列
  → 唯一的推导轨迹与最终状态
```

轨迹应包含足以解释和重放每一步状态变化、信号消费及信号产生的信息。具体轨迹 schema 在 derive 详细计划中定义。

并发行为不以一次非确定执行作为验证结果，而是转换为确定信号序列的枚举，并对每条序列分别执行上述单路径验证。枚举顺序、边界和去重规则必须显式配置且可复现。

推导结果必须区分至少三种终止原因：

- **静止（quiescence）**：当前没有待处理事件，系统按模型语义自然停止。
- **死锁（deadlock）**：仍存在未完成的等待或依赖，但系统不可能继续推进。
- **搜索截断（truncated）**：因为步数、深度、状态数、时间或其他搜索预算而停止，不能据此宣称静止或死锁。

更完整的确定性条件、最终静止定义、并发序列枚举和逐路径验证规则见[推导引擎设计备忘录第 9–10 节](../tools/signal-system-inference-engine.md#9-确定性路径推导与序列穷尽)。

## 6. 分阶段实施

### 阶段一：modelc 与 Model IR

1. 为当前 `.spec` 语言形成独立 grammar、AST 和源位置模型。
2. 实现跨文件装载、名字解析、类型与语义检查。
3. 定义满足当前模型和首版 derive 所需的最小 Model IR schema。
4. 实现 `compile_spec()`、`load_model_ir()` 和 `dump_model_ir()`。
5. 提供薄的 `tools/bin/modelc` 命令行入口和分层诊断。
6. 决定 Pydantic 是否进入正式依赖及其使用边界。

首阶段成功标准：当前所有 `model/**/*.spec` 均可解析并生成 Model IR；同一输入重复编译得到等价的 Model IR 和可重复的规范 JSON。

### 阶段二：指定序列 derive

1. 定义初始快照、输入信号序列、轨迹和最终状态的数据结构。
2. 实现单步迁移与指定序列执行。
3. 同时支持 `.spec` 的进程内编译和 `.json` 的直接加载。
4. 提供薄的 `tools/bin/derive` 命令行入口。

首阶段成功标准：相同 Model IR、初始快照和信号序列始终产生相同轨迹与最终状态；从 `.spec` 和其对应 JSON IR 开始推导得到相同结果。

### 阶段三：穷尽搜索

1. 枚举受边界约束的确定信号序列。
2. 增加状态去重、循环检测、搜索预算和反例保存。
3. 对每条路径复用阶段二的确定性推导器。
4. 在结果和退出状态中明确区分静止、死锁、失败与搜索截断。

成功标准：在相同模型、初始状态、搜索策略和预算下，搜索覆盖与结果可重复；每个报告的路径都能由保存的序列单独重放。

### 阶段四：评估 rustgen

在 Model IR 和推导语义稳定后，再确定 Rust 生成目标、运行时边界和生成代码的验证办法。`rustgen` 应消费现有 Model IR；若代码生成暴露 IR 缺口，应通过显式 schema 演进补充，而不是依赖 `.spec` 解析器内部结构。

## 7. 验收与测试边界

总体实施至少需要覆盖以下验收项：

- `modelc` 能编译仓库中每个 `model/**/*.spec` 入口或被包含单元，并对结果执行结构验证。
- 重复编译不受文件遍历顺序、哈希迭代顺序或临时路径影响。
- Model IR JSON 可以由 `dump_model_ir()` 输出，再由 `load_model_ir()` 恢复为等价对象。
- 语法、名字、类型和语义错误均由 modelc 报告，不推迟到 derive 中偶然失败。
- `derive` 的库调用和两个输入模式共享同一执行核心。
- 相同输入产生逐步一致的轨迹、终止原因和最终状态。
- 穷尽搜索产生的每条路径可作为指定序列重放，搜索截断不会被报告为验证成功。

性能优化、完整 schema 兼容承诺、Rust 运行时设计和生成代码 API 不属于当前总体计划的首期验收范围。

## 8. 下一步

下一份计划应专门设计 modelc，至少明确：

- `grammar.lark` 覆盖的当前语法和语法测试；
- AST 节点、源范围及跨文件装载规则；
- 名字空间、名字解析、类型系统与语义检查；
- Model IR 的首版 schema、JSON 版本与规范序列化；
- 诊断数据结构、命令行呈现和退出码；
- 单元测试、当前模型语料测试和可重复性测试。

在 modelc 详细计划通过并实现前，不需要提前展开 derive 的完整执行器或 rustgen 设计。
