# 发票争议中的项目事实与往来依据

本地文本工作区用于保存供应商往来语义归档相关的合成记录，并提供基础规范化和词项查看能力。

## 环境

需要 Python 3.11 或更新版本。运行程序仅使用 Python 标准库。测试依赖固定为 pytest 8.3.4，可执行 `python -m pip install -r requirements-dev.txt` 安装。

## 运行

用 `PYTHONPATH=src python -m nlp_invoice_dispute_brief.case_cli` 查看人工核对折让后的每行可付款、已入账金额和补差。加 `--review` 输出完整复核报告（主张台账、折让占用、候选场景暂留、锁定冲突与依据），用 `--prior 文件.json` 传入上一期已确认分配以启用锁定校验。邮件摘句以原文的 Unicode 字符位置登记，左端包含、右端省略。转发区间另行标注。金额单位为分。`fixtures/discounts.json` 是一份含两个收货批次、转发转述与折让重述的合成对账材料，字段约定见 fixtures/business-contract.md。

复核规则要点：

* 主张按 `提出 / 同意 / 转述` 区分立场，转发区间内的句子只能登记为转述，未经供货方同意不得据以扣款；每条主张保留货款、运费、折让类别、适用条件、金额与批次候选。
* `restates` 把多封邮件中的同一笔折让归并为一个身份，重述金额必须一致，占用合并计算，防止"改分给两批"被再扣一次。
* `candidate_sets` 中的多种解释分别试算：候选分配对某行可付余额没有影响时该行照常结算，只有分歧部分暂留。
* 上一期已确认分配保持锁定，新材料与之冲突时报错并在冲突报告中指明涉及金额与原文，不能悄悄替换。
* 已入账付款按原指令查阅，新结论的差额走补差（`adjustment_cents`），不重复花用已占用的折让。


从项目根目录执行 `PYTHONPATH=src python -m nlp_invoice_dispute_brief.cli ingest fixtures/records.json` 导入样例，随后执行 `PYTHONPATH=src python -m nlp_invoice_dispute_brief.cli list` 查看记录。工作数据写入 `var/records.json`。业务查询使用 fixtures/business.json 中的初始资料，约定见 fixtures/business-contract.md。执行 PYTHONPATH=src python -m nlp_invoice_dispute_brief.cli business 可查看当前业务目录结果。

样例为本地合成文本。记录包含来源、修订号和原文，当前工作区对空白做统一处理，并提供字符及英文词项切分。

## 测试

执行 `python -m pytest -q` 检查规范化、记录持久化、来源筛选、立场区分、折让重述防重扣、候选暂留、锁定冲突与补差。
