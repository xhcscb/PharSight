# 医药投研 Claim–Evidence Graph Schema v1

版本：1.0.0。状态：可实现的第一版规范，待多文档试抽取迭代。依据：本轮构图讨论与成员 E 的《副本combined_resources.xlsx》。本规范定义自己的图格式，不保证直接兼容 Youtu 的输入或检索器。

## 1. 如何使用文件

- `graph_schema_v1.md`：人读的设计约定，也可择取必要规则写入抽取提示词。
- `graph_schema_v1.json`：JSON Schema，检查程序输出的字段、类型、枚举和模板条件。它不是抽好的图。
- `graph_example_v1.json`：一条临床 Claim 的实际格式示例，含来源节点、实体、边和链接记录。它不是整篇文档的抽取结果，也不是经过独立医学核验的金标准。

流水线：解析文档与文段 → 抽取候选 Claim 和原始实体提及 → 查别名库并消歧 → 输出节点及明确类型的边 → JSON Schema 与图完整性检查 → claim–evidence 检查 → 正式索引。审核未完成的候选单独保留。查询时可以复用别名库解析问题中的实体。

## 2. 图的结构与身份

Document → Passage → Claim → Entity 是组织主线，不是严格树。一个文段可以表达多条 Claim；一条 Claim 可有多个证据文段；同一个 Entity 被多个 Claim 共享；Claim 之间可以比较或引用依据。

Attribute 是字段，不是第五层节点。Alias、Mention 和 Evidence 在 v1 中也是记录或字段，不新增节点。JSON 的嵌套结构不等于图层级。

| node_type | 用途 | 必要字段 |
| --- | --- | --- |
| Document | 来源文档或明确标记的摘录 | id、title、source_name、source_uri、publication_date、source_kind |
| Passage | 可定位、保持原貌的文本片段 | id、text、locator（page、section、block_label） |
| Claim | 一条带条件的来源陈述 | id、text、domain、claim_type、evidence、attributes、verification |
| Entity | 可跨主张共享的对象 | id、entity_type、canonical_name、identity_status、external_ids |

以上都含 node_type。未知元数据用 null；日期优先保留原文，标准化日期不得凭空推断。Document 日期未知时，不把“今年”硬转为某年。

Entity 子类型：Drug、Company、Trial、Target、Indication。Drug 暂按药物成分/研发候选物粒度；具体产品品牌不能无条件视为同一身份。联合用药保留组合描述与各成分，不把组合并成其中一种药。Target 暂用于靶点概念，不自动合并基因、蛋白及配体。

Claim、Passage、边和提及 ID 应带文档命名空间或使用 UUID，不能跨文档重复使用裸 c1、p1。Entity 正式 ID 沿用共享登记表，禁止每篇文档重编号。

## 3. Claim 分类与粒度

| domain | 内容 |
| --- | --- |
| clinical | 临床疗效、安全性、结果及比较 |
| development_regulatory | 研发、试验进展、申报与审批 |
| commercial_investment | 销售、市场竞争、投资判断 |
| mechanism | 靶点、机制 |

| claim_type | 边界 |
| --- | --- |
| result_report | 报告具体结果；推算结果也可属于此类，用 value_nature 区分 |
| comparison | 比较对象或结果，保留比较设计与限定语 |
| event | 报告已发生事件；未确定或未来行动不能当已发生事件 |
| forecast | 未来事件或结果的预期 |
| evaluation | 对地位、价值、表现等的评价 |
| relation_assertion | 实体关系陈述，保留谓词、否定与条件 |

domain 与 claim_type 是两条轴，不一一对应。临床使用 PICO 思路组织条件；其他 domain 不强套 PICO。一个 Claim 保留一个可独立核对的主张和完整条件，不按每个名词或数字拆节点。不同试验/组别/时间点的结果分别记录；一个指标的点估计与其置信区间可同存。

“结果A + 结果B + 比较结论”通常为三条 Claim；只有“A优于B”时只建比较 Claim，不能编造两条数值结果。比较结果不存在时通过 has_subject 连接被比较实体，并在 conclusion 中明确对象和方向。预测性的比较优先归 forecast，并在预测文字和限定语中保留比较内容；单一主张不为凑类型重复建节点。

## 4. Claim 属性模板

所有模板固定字段名，禁止模型随意扩展键。未覆盖的新内容保留在 text 与 qualifiers/conditions，并进入 schema 扩展审核，不能为了通过格式检查丢弃关键条件。

### 4.1 临床 result_report

临床结果按 **PICO＋result** 组织，`Claim.attributes` 只保留以下五项：

| 字段             | 含义与规则                                                                                 |
| -------------- | ------------------------------------------------------------------------------------- |
| `population`   | **P：研究人群。** 保存原文明示的 BMI、年龄、合并症等条件；未说明为 `null`。                                        |
| `intervention` | **I：干预措施。** 保存药物实体引用及方案描述，包括剂量、频率、给药方式、联合用药等，并明确各条件对应的药物。                             |
| `comparator`   | **C：对照措施。** 保存对照药物引用或文字描述；安慰剂、生活方式干预无需强建 Drug 节点。未说明为 `null`，不得将跨试验比较的另一种药物自动当作本试验对照。 |
| `outcome`      | **O：结局指标。** 例如“减重幅度”；作为字段保存，不独立建节点。                                                   |
| `result`       | **结果及解释条件。** 保存数值、单位、时间点、是否约数、推测或报告性质，以及原文明示的估计目标、安慰剂校正情况和其他统计限定。                     |

`evidence` 与 `verification` 是 Claim 的公共字段，位于 `attributes` 之外。五项内部的实体引用应与对应的 Claim–Entity 连边一致。

**记录原则：** 未说明的信息不自行补全；`null` 不表示“否”。减少字段不应丢失原文条件，暂未结构化的限定可保留为相应字段内的文字描述。

例如：

| 字段             | 内容                                        |
| -------------- | ----------------------------------------- |
| `population`   | 未说明                                       |
| `intervention` | 替尔泊肽（`DRUG_002`），15 mg                    |
| `comparator`   | 未说明                                       |
| `outcome`      | 减重幅度                                      |
| `result`       | 48周约18%，为推测值；原文称“同口径下”，未明确估计目标及是否经过安慰剂校正。 |


### 4.2 其他模板

| claim_type | 必填属性字段（允许未知项为 null） |
| --- | --- |
| comparison | outcome、comparison_design、conclusion、qualifiers |
| event | event_kind、event_date_text、normalized_date、stage_or_status、qualifiers |
| forecast | forecast_content、target_time_text、normalized_target_time、predicted_quantity、conditions、qualifiers |
| evaluation | dimension、judgment、scope、qualifiers |
| relation_assertion | predicate、polarity、conditions、qualifiers |

comparison_design：head_to_head / cross_trial / other / not_stated。不能从两个数字就推断优效，也不能把“未达到非劣效终点”改写为“已证明劣效”。relation_assertion.predicate 在 v1 仅开放下表五种实体关系；新谓词先审核后扩展。

## 5. 连边

关系清单共五类、18个边名。其中13个为结构/来源/参与/主张关联，5个为可选实体派生边。开放清单不表示每张图必须生成全部关系。

| relation | 允许端点 | 含义 |
| --- | --- | --- |
| `contains` | Document → Passage | 文档包含文段 |
| `expresses` | Passage → Claim | 原文表达主张，不等于事实为真 |
| `has_intervention` | Claim → Drug | 临床干预药物 |
| `has_comparator` | Claim → Drug | 临床对照药物 |
| `in_trial` | Claim → Trial | 结果或事件所属试验 |
| `has_population_condition` | Claim → Indication | 人群疾病条件 |
| `announced_by` | Claim → Company | 公告发布方 |
| `has_subject` | Claim → Entity | 评价、预测或关系陈述的主体 |
| `has_object` | Claim → Entity | 关系陈述的客体 |
| `compares_result` | Claim → Claim | 比较主张引用被比较结果 |
| `uses_as_basis` | Claim → Claim | 结论主张引用依据 |
| `contradicts` | Claim → Claim | 经审核确认的冲突，语义对称 |
| `supersedes` | Claim → Claim | 新主张明确更新或替代旧主张 |
| `targets` | Drug → Target | 可选派生边 |
| `developed_by` | Drug → Company | 可选派生边 |
| `sponsored_by` | Trial → Company | 可选派生边 |
| `studies_drug` | Trial → Drug | 可选派生边 |
| `combined_with` | Drug → Drug | 可选派生边，语义对称 |

约束：

1. contains：每个 Passage 恰好一个父 Document；可直接由分段结果生成。
2. expresses：每个 Claim 至少一个来源 Passage；与 evidence 中的 passage_id 对齐，由证据引用生成，不另让模型猜。
3. has_intervention / has_comparator 用于临床结果或比较。单条结果原则上最多一个 in_trial；没有明确试验时可为零。跨试验比较的对方药物不是本试验的对照，不能误连 has_comparator。
4. has_population_condition 只连接适应证/疾病概念，完整纳入标准仍放 population。announced_by 仅用于有发布行为的事件 Claim，不能替代 sponsored_by / developed_by。
5. has_subject 用于评价、预测、比较及关系陈述的主体；has_object 用于 relation_assertion 的客体。二元关系陈述恰好一个主体、一个客体，按 predicate 的实体端点类型检查。不再复制一套 role。
6. compares_result 的起点必须是 comparison，终点必须是 result_report；comparison_index 为该比较内不重复的正整数，conclusion 应能明确各比较对象。两个以上结果允许多条边。只对已有且可指认的结果建立连接。
7. uses_as_basis：方向为结论 → 依据。要求来源表达了依赖关系，不能只因主题相同就建；证据放在边的 evidence 中。
8. contradicts 语义对称，只存一条（ID按字典序排列）；须经可比性与冲突审核，origin=reviewed，attributes.reason 必填。supersedes 从明确更新后的 Claim 指向旧 Claim，同样须审核，发布时间较新不自动构成替代。
9. Entity → Entity 为可选派生索引。origin=derived，asserted_by_claim_ids 非空，指向支持该关系的 relation_assertion；默认只从 gate_status=passed、polarity=positive 且身份已确认的主张派生。边本身不代表无条件真理，检索仍须返回原 Claim 的条件与来源。combined_with 语义对称，只存一条。
10. 不建立笼统 related_to、共现即关系、所有同药 Claim 两两连接。共享 Entity 已经提供连通路径。逆向遍历可由程序实现，不必重复存反向边。

边公共字段：id、source、target、relation、origin、evidence、asserted_by_claim_ids、attributes。origin 取 structural / extracted / derived / reviewed。节点属性不再额外生成 has_attribute 边。

## 6. 接入成员 E 的别名库

来源：副本combined_resources.xlsx / Alias Mapping。已读入63条记录、14个canonical_id，具有Drug/Company/Target/Indication四类，目前没有Trial。检查确认同ID对应的标准名和实体类型在当前表中一致；本次没有逐条外部核实医学别名。

| canonical_id | canonical_name | entity_type |
| ------------ | -------------- | ----------- |
| DRUG_001     | 司美格鲁肽          | Drug        |
| DRUG_002     | 替尔泊肽           | Drug        |
| DRUG_003     | 玛仕度肽           | Drug        |
| DRUG_004     | 利拉鲁肽           | Drug        |
| DRUG_005     | 瑞他鲁肽           | Drug        |
| COMP_001     | 诺和诺德           | Company     |
| COMP_002     | 礼来             | Company     |
| COMP_003     | 信达生物           | Company     |
| TARGET_001   | 胰高血糖素样肽-1受体    | Target      |
| TARGET_002   | 葡萄糖依赖性促胰岛素多肽受体 | Target      |
| TARGET_003   | 胰高血糖素受体        | Target      |
| IND_001      | 2型糖尿病          | Indication  |
| IND_002      | 肥胖症            | Indication  |
| IND_003      | 体重管理           | Indication  |

使用规则：

- 构图正式 Entity.id = 别名库 canonical_id。例如替尔泊肽和 Tirzepatide 确认后均指向 DRUG_002。两个 Claim 共享一个 Entity，不另外建两个名称节点或 same_as 边。
- canonical_name 可维护，ID 不随名字更改。外部编号以 namespace/value 列表存于 external_ids；不能把本地 DRUG_002 冒充 DrugBank 编号。
- 别名库用于身份解析，不能用它补写研报未表达的公司、药物、试验关系。
- 原始提及保存在 entity_links，包含 claim_id、passage_id、mention_text、entity_id、link_status、match_method、mapping_kind、alias_row、reason。这些记录不增加图节点。
- graph.alias_library 保存文件名、sheet和文件SHA-256，明确此次构图实际使用的表格版本。alias_row 是该快照的Excel行号，正式库后续可增加稳定alias_id。
- 轻量格式归一后查标准名/别名，再检查类型与上下文。精确命中也可能歧义；Embedding只召回候选，阈值不保证身份正确。
- 只有经确认的 same_entity 映射能合并。Brand Name 不直接当严格同义词，品牌级销售/审批应保留产品范围；超出当前Drug粒度的内容待扩展产品类型。当前表缺 mapping_kind、review_status 和条目级出处，不能据此认定所有行均已审核。它们是建议 E 后续补充的库字段；不直接修改现有表。
- 未命中保留 TMP_<document>_<type>_<number> 临时实体，identity_status=provisional，link_status=unresolved 或 needs_review。它们仍可接候选 Claim，但不视为跨文档身份确认。审核后重定向相关边、intervention_specs 和 entity_links，并保存迁移记录，禁止遗留旧引用。
- Trial需要补登记；不要让模型伪造NCT编号。当前示例中的SURMOUNT-1使用临时ID，明确等待登记。

## 7. Evidence 与 gate

Claim.evidence 至少一条：passage_id + quote；可选 field_paths 指明专门支持的属性路径。quote 必须能在对应 Passage.text 中定位。一般属性继承整条Claim的证据；跨句/表格/脚注再增加证据，避免每个字段重复一整套出处。

verification 包含 gate_status 和 reason。状态：not_checked / passed / needs_review / rejected。抽取器默认 not_checked；检查者填写其余状态。该状态检查的是“来源是否支持此表述”，不是断言该研究在现实中一定真实。

Gate步骤：检查引用存在 → 检查主体、数值、条件、否定与推测性质是否忠实 → 检查边是否有依据 → 决定正式索引准入。needs_review / rejected 保留在候选或审核区，不混作已通过事实；通过结构检查不能自动设置 passed。

跨来源：不同来源的Claim分别保存。先对齐药物、试验、组别、剂量、结局、时间点、估计目标、校正方式与数据截止，再判断是否冲突。条件不同通常不是冲突；关键条件未知先待审核。两条Claim都可忠实于各自原文而彼此矛盾。冲突不删除原始记录；转引同一研究不视为多份独立证据。

## 8. JSON Schema 与运行时校验的分工

| JSON Schema 已定义 | 仍需构图程序/审核步骤执行 |
| --- | --- |
| 四类节点结构、枚举、字段类型、禁止任意多余字段 | ID全局唯一；引用存在；所有边端点类型合法 |
| 六类Claim属性模板与临床必填条件 | 关系对应的Claim类型、数量和主客体约束 |
| 边名白名单；禁用has_participant/role | expresses与evidence一致；证据quote真实可定位 |
| 比较序号、派生依据、审核理由的格式要求 | 序号在单次比较内不重复；派生边真有合格依据 |
| 提及链接及别名版本记录格式 | 正式ID与当前登记表一致；歧义/商品名处理 |
| verification字段的合法值 | 语义蕴含、医学限定语、跨来源可比性与冲突 |

v1交付的是规范和格式校验规则，尚未实现通用抽取器、实体消歧器、语义verifier和完整图运行时校验器。不要仅凭JSON Schema通过就认定图正确。

## 9. 示例与本次验证

示例选取SURMOUNT-1的48周约18%结果：1 Document、1 Passage、1 Claim、2 Entity、4条边、2条提及链接。替尔泊肽使用表中DRUG_002；试验使用临时ID。推测性质、约数、剂量和时间保留；不把另一项研究的估计目标或校正方式转移过来。

本次检查：JSON Schema自身合法；示例通过结构校验；示例引用/端点与引文位置检查通过；旧role字段、非法claim_type、缺少临床estimand字段的三个反例均被拒绝。示例gate仍为not_checked，不声称独立语义验证完成。

接下来应固定本版本，在同一批文段上运行自动抽取，统计条件丢失、错连、身份误合并、缺漏等错误，再做版本修订。比较Youtu时使用相同文段和问答条件；手工示例不能作为自动构图性能证据。
