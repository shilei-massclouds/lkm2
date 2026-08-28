# DtbBlob 编码约束

本页对应 [`model/objects/dtb_blob.spec`](../../model/objects/dtb_blob.spec) 和
[`charter/objects/dtb_blob.md`](../../charter/objects/dtb_blob.md)。实现映射为
[`impl/objects/dtb_blob.rs`](../../impl/objects/dtb_blob.rs)。

## 输入与所有权

`setup_vm(dtb_pa) -> usize` ABI 保持不变。Vm 负责建立两个连续 PMD 的只读 DTB fixmap，
`EarlyDtbMapping` 只发布从原始 DTB 地址开始的虚址和剩余有效窗口；`DtbBlob` 借用该静态
窗口进行校验，不取得映射或固件内存所有权。

## FDT 校验与输出

`DtbBlob::from_bytes` 必须在发布任何输出前校验 magic、版本、total size、各 block 的 offset
与 size、reservation map 终止项、structure token、节点嵌套和 strings table 引用。只接受
唯一根节点下唯一 `/chosen/bootargs` 属性；缺失、重复或畸形输入整体失败。

成功解析的 `DtbBlob` 只向下游发布原始 bootargs 属性切片。`BootCommandLine` 独立负责
4096 字节上限、NUL/UTF-8 和唯一 `earlycon=sbi` token 校验并复制内容。该边界保持
`DtbBlob → BootCommandLine → EarlyConTable` 的单向生产者—消费者关系。

## 失败与范围

任何 DTB 或 bootargs 失败均在 SBI probe 前进入 interrupt-masked fail-stop，不提交 Console
注册。当前实现不解析 CPU、memory、interrupt controller 或其他设备节点，也不以 DtbBlob
Online 暗示 DTB 映射生命周期结束。
