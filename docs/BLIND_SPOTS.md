# 题库盲区补课清单

> 由 `python -m scripts.coverage_gaps` 自动生成。

**「刷完这 N 道题就能高分」的前提是题池覆盖得住考纲。这里先验证这个前提。**

| 项 | 值 |
|---|---|
| 考纲考点总数 | 93 |
| 题池能覆盖 | 57 |
| **题池完全够不到** | **36（39%）** |
| 题池规模 | 550 |

覆盖优化只能在题池够得到的范围内做到最优。
下面这些考点**刷多少题都碰不到**，只能靠读考纲和官方文档补。

## 按 Domain 分布

| Domain | 盲区 / 总数 | |
|---|---|---|
| D1  | 6/18 | `███████` |
| D2  | 14/30 | `█████████` |
| D3  | 14/26 | `███████████` |
| D4  | 2/19 | `██` |

## 盲区考点（36 个，其中 36 个已配补课要点）

每条链接都用 `curl` 验证过返回 200。

### D1 · T1.1 Architect network connectivity strategies

#### `T1.1.K1` AWS Global Infrastructure

**要点**：Region / AZ / 本地扩展区 / Wavelength / Outposts 各自解决什么问题

**为什么重要**：几乎所有多区域题的隐含前提。分不清 AZ 与 Region 的故障域边界，多活与容灾题会整片错。

**自测**：
- AZ 之间的延迟量级是多少？跨 Region 呢？
- Local Zone 与 Outposts 的选择依据是什么？

**文档**：[global_infra](https://aws.amazon.com/about-aws/global-infrastructure/) · [wa_reliability](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)

#### `T1.1.K5` Network traffic monitoring

**要点**：VPC Flow Logs、Traffic Mirroring、Reachability Analyzer 的适用场景

**为什么重要**：「排查两个 VPC 之间为什么不通」这类题的标准答案来源。

**自测**：
- Flow Logs 能看到包内容吗？要看内容用什么？
- 路径不通但没有 REJECT 记录，说明问题出在哪一层？

**文档**：[flow_logs](https://docs.aws.amazon.com/vpc/latest/userguide/flow-logs.html) · [route_tables](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Route_Tables.html)

### D1 · T1.2 Prescribe security controls

#### `T1.2.K2` Route tables, security groups, and network ACLs

**要点**：路由表 / 安全组 / 网络 ACL 三者的求值顺序与有状态性差异

**为什么重要**：安全组有状态、NACL 无状态，这个差别几乎每次考试都出现在某道题的干扰项里。

**自测**：
- NACL 只放行入站 80，出站没开，请求能通吗？安全组呢？
- 两者的规则是按顺序求值还是取并集？

**文档**：[sg](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html) · [nacl](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-network-acls.html) · [route_tables](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Route_Tables.html)

### D1 · T1.3 Design reliable and resilient architectures

#### `T1.3.K1` Recovery time objectives (RTOs) and recovery point objectives (RPOs)

**要点**：RTO / RPO 的定义，以及它们如何决定容灾架构选型

**为什么重要**：题干给出 RTO/RPO 数字，就是在告诉你答案该选哪一档容灾方案。读不出这个信号就只能靠猜。

**自测**：
- RPO 接近 0 但 RTO 允许几小时，该选哪种方案？
- Backup&Restore / Pilot Light / Warm Standby / Multi-Site 的 RTO 量级分别是？

**文档**：[dr_whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-workloads-on-aws.html) · [wa_reliability](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)

### D1 · T1.4 Design a multi-account AWS environment

#### `T1.4.K2` Multi-account event notifications

**要点**：跨账户事件通知：EventBridge 事件总线跨账户、Organizations 与 SNS 的组合

**为什么重要**：多账户安全响应题的核心机制。

**自测**：
- 成员账户的事件如何汇聚到管理账户？
- 委派管理员和 EventBridge 跨账户规则如何配合？

**文档**：[eventbridge](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-what-is.html) · [org_services](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_integrate_services.html)

#### `T1.4.K3` AWS resource sharing across environments

**要点**：AWS RAM 能共享什么、不能共享什么

**为什么重要**：「跨账户共享子网 / TGW / License」这类题只有 RAM 一个答案，而 RAM 的可共享资源清单是硬知识。

**自测**：
- RAM 能共享 Lambda 函数吗？能共享子网吗？
- RAM 共享与跨账户 IAM 角色的适用边界？

**文档**：[ram](https://docs.aws.amazon.com/ram/latest/userguide/what-is.html) · [org_services](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_integrate_services.html)

### D2 · T2.1 Design a deployment strategy to meet business requirements

#### `T2.1.K2` Continuous integration and continuous delivery (CI/CD)

**要点**：CI/CD 全链路：CodeCommit / CodeBuild / CodeDeploy / CodePipeline 各自的职责

**为什么重要**：D2 的高频考点，题库完全没覆盖。

**自测**：
- 跨账户部署流水线怎么搭？
- 制品在账户之间如何传递，KMS 密钥策略要注意什么？

**文档**：[codepipeline](https://docs.aws.amazon.com/codepipeline/latest/userguide/welcome.html) · [codedeploy](https://docs.aws.amazon.com/codedeploy/latest/userguide/welcome.html) · [wa_operations](https://docs.aws.amazon.com/wellarchitected/latest/operational-excellence-pillar/welcome.html)

### D2 · T2.2 Design a solution to ensure business continuity

#### `T2.2.K1` AWS Global Infrastructure

**要点**：从业务连续性角度看全球基础设施：故障域隔离

**为什么重要**：与 T1.1.K1 同源，但出题角度是「灾难发生时哪些东西会一起挂」。

**自测**：
- 单 Region 多 AZ 能抵御什么级别的故障？不能抵御什么？

**文档**：[global_infra](https://aws.amazon.com/about-aws/global-infrastructure/) · [dr_whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-workloads-on-aws.html)

#### `T2.2.K3` RTOs and RPOs

**要点**：把 RTO/RPO 目标翻译成具体的 AWS 服务组合

**为什么重要**：与 T1.3.K1 成对出现，一个考概念一个考落地。

**自测**：
- RPO=0 的数据库方案有哪些？代价分别是什么？

**文档**：[dr_whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-workloads-on-aws.html) · [rds_multiaz](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html)

#### `T2.2.K5` Disaster recovery solutions on AWS

**要点**：四种容灾策略的完整对比：成本、RTO、RPO、复杂度

**为什么重要**：整个 D2 里权重最高的单一考点，题库一道都没有。

**自测**：
- Pilot Light 和 Warm Standby 的本质区别是什么？
- 跨 Region 故障转移时，Route 53 与 ARC 各起什么作用？

**文档**：[dr_whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-workloads-on-aws.html) · [arc](https://docs.aws.amazon.com/r53recovery/latest/dg/what-is-route53-recovery.html) · [wa_reliability](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)

### D2 · T2.3 Determine security controls based on requirements

#### `T2.3.K2` Route tables, security groups, and network ACLs

**要点**：从「设计新方案」角度选安全控制：分层防御

**为什么重要**：与 T1.2.K2 同知识、不同出题角度。

**自测**：
- 三层架构中每一层分别该用什么网络控制？

**文档**：[sg](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html) · [nacl](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-network-acls.html) · [wa_security](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html)

#### `T2.3.K3` Encryption options for data at rest and data in transit

**要点**：静态加密（SSE-S3 / SSE-KMS / SSE-C / 客户端）与传输加密（TLS、VPN、MACsec）

**为什么重要**：合规类题必考。SSE-KMS 与 SSE-S3 的密钥控制权差异是常见干扰项。

**自测**：
- 需要审计谁在什么时候解密了对象，该选哪种？
- S3 Bucket Key 解决什么问题？

**文档**：[s3_sse_kms](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingKMSEncryption.html) · [kms](https://docs.aws.amazon.com/kms/latest/developerguide/overview.html) · [wa_security](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html)

#### `T2.3.K4` AWS service endpoints

**要点**：Gateway Endpoint 与 Interface Endpoint（PrivateLink）的区别与计费

**为什么重要**：「不经公网访问 S3 / 其他账户的服务」是高频场景。

**自测**：
- S3 用哪种端点？为什么它免费？
- 跨账户暴露自建服务用什么？

**文档**：[privatelink](https://docs.aws.amazon.com/vpc/latest/privatelink/concepts.html)

#### `T2.3.K5` Credential management services

**要点**：Secrets Manager vs Parameter Store：自动轮换、跨账户、成本

**为什么重要**：凭据管理题的标准二选一。

**自测**：
- 需要自动轮换 RDS 密码选哪个？
- Parameter Store 的 SecureString 与 Secrets Manager 的取舍点是什么？

**文档**：[secrets](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html) · [kms](https://docs.aws.amazon.com/kms/latest/developerguide/overview.html)

### D2 · T2.4 Design a strategy to meet reliability requirements

#### `T2.4.K1` AWS Global Infrastructure

**要点**：从可靠性角度设计跨 AZ / 跨 Region 部署

**为什么重要**：与 T1.1.K1、T2.2.K1 三处同源，说明这是考纲反复强调的基础。

**自测**：
- 静态稳定性（static stability）是什么意思？为什么它影响 AZ 数量选择？

**文档**：[wa_reliability](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html) · [global_infra](https://aws.amazon.com/about-aws/global-infrastructure/)

#### `T2.4.K3` Multi-AZ and multi-Region architectures

**要点**：多 AZ 与多 Region 架构的取舍：数据一致性、延迟、成本

**为什么重要**：多活架构题的核心权衡。

**自测**：
- Aurora Global Database 的 RPO 大约是多少？
- DynamoDB 全局表的冲突解决策略是什么？

**文档**：[rds_multiaz](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html) · [wa_reliability](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html) · [dr_whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-workloads-on-aws.html)

### D2 · T2.5 Design a solution to meet performance objectives

#### `T2.5.K1` Performance monitoring technologies

**要点**：性能监控：CloudWatch 指标粒度、自定义指标、Synthetics、X-Ray

**为什么重要**：「如何发现性能瓶颈」这类题的服务选型依据。

**自测**：
- 详细监控与基础监控的粒度差别？
- 要从用户视角持续探测可用性用什么？

**文档**：[cw_alarms](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html) · [cw_synthetics](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Synthetics-Canaries.html) · [wa_performance](https://docs.aws.amazon.com/wellarchitected/latest/performance-efficiency-pillar/welcome.html)

#### `T2.5.K2` Storage options on AWS

**要点**：存储选型全景：EBS 各卷类型、EFS、FSx 家族、S3、Instance Store

**为什么重要**：存储选型是 SAP 最常考的单一维度之一。

**自测**：
- 需要 Windows 共享文件系统选什么？Lustre 用于什么场景？
- io2 Block Express 与 gp3 的选择依据？

**文档**：[s3_classes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html) · [wa_performance](https://docs.aws.amazon.com/wellarchitected/latest/performance-efficiency-pillar/welcome.html)

### D2 · T2.6 Determine a cost optimization strategy to meet solution goals and objectives

#### `T2.6.K3` Storage tiering

**要点**：S3 存储分层与生命周期规则、Intelligent-Tiering 的适用条件

**为什么重要**：成本优化题的头号答案来源。

**自测**：
- 访问模式不可预测时该选什么？
- Glacier Deep Archive 的取回时间与最小存储周期？

**文档**：[s3_classes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html) · [s3_lifecycle](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html) · [wa_cost](https://docs.aws.amazon.com/wellarchitected/latest/cost-optimization-pillar/welcome.html)

#### `T2.6.K5` AWS managed service offerings

**要点**：用托管服务替代自建以降低总成本（TCO 而非单价）

**为什么重要**：「最小运营开销」类题的判定标准。

**自测**：
- 自建 Kafka 与 MSK 的成本比较该算哪些项？

**文档**：[wa_cost](https://docs.aws.amazon.com/wellarchitected/latest/cost-optimization-pillar/welcome.html) · [compute_optimizer](https://docs.aws.amazon.com/compute-optimizer/latest/ug/what-is-compute-optimizer.html)

### D3 · T3.1 Determine a strategy to improve overall operational excellence

#### `T3.1.K1` Alerting and automatic remediation strategies

**要点**：告警与自动修复：CloudWatch Alarm + EventBridge + SSM Automation / Config Remediation

**为什么重要**：「自动修复不合规资源」是持续改进域的标志性题型。

**自测**：
- Config 规则检测到不合规后如何自动修复？
- EventBridge 与 CloudWatch Alarm 谁触发谁？

**文档**：[config_remediation](https://docs.aws.amazon.com/config/latest/developerguide/remediation.html) · [eventbridge](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-what-is.html) · [cw_alarms](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html)

#### `T3.1.K2` Disaster recovery planning

**要点**：容灾计划的运营面：演练、Runbook、故障切换测试

**为什么重要**：与 T2.2.K5 互补，考的是「方案定了之后怎么运营」。

**自测**：
- 如何在不影响生产的前提下验证故障转移？

**文档**：[dr_whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-workloads-on-aws.html) · [wa_operations](https://docs.aws.amazon.com/wellarchitected/latest/operational-excellence-pillar/welcome.html)

#### `T3.1.K4` CI/CD pipelines and deployment strategies (for example, blue/green, all-at-once, rolling)

**要点**：部署策略：蓝绿、金丝雀、滚动、全量的适用场景与回滚代价

**为什么重要**：题干里的「零停机」「快速回滚」「逐步放量」直接对应不同策略。

**自测**：
- 蓝绿与金丝雀在成本和回滚速度上的差别？
- Lambda 的加权别名属于哪一种？

**文档**：[codedeploy](https://docs.aws.amazon.com/codedeploy/latest/userguide/welcome.html) · [wa_operations](https://docs.aws.amazon.com/wellarchitected/latest/operational-excellence-pillar/welcome.html)

### D3 · T3.2 Determine a strategy to improve security

#### `T3.2.K1` Data retention, data sensitivity, and data regulatory requirements

**要点**：数据留存、敏感度分级与合规要求（数据主权、留存期限）

**为什么重要**：合规题的前提知识，决定了能不能跨 Region 复制。

**自测**：
- 数据必须留在某国境内时，哪些 AWS 特性会违规？
- S3 Object Lock 的两种模式差别？

**文档**：[wa_security](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html) · [s3_replication](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication.html)

#### `T3.2.K4` Principle of least privilege access

**要点**：最小权限：权限边界、SCP、会话策略、Access Analyzer

**为什么重要**：IAM 高级题几乎都在考「多个策略同时存在时最终有效权限是什么」。

**自测**：
- SCP 能给用户授权吗？
- 权限边界与身份策略求交还是求并？

**文档**：[iam_best](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html) · [wa_security](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html)

#### `T3.2.K5` Security-specific AWS solutions

**要点**：安全服务全景：GuardDuty / Security Hub / Detective / Inspector / Macie 各自的定位

**为什么重要**：「威胁检测 + 集中管理 + 自动响应」组合题的选项来源。

**自测**：
- GuardDuty 与 Inspector 分别检测什么？
- Security Hub 在多账户下靠什么聚合？

**文档**：[org_services](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_integrate_services.html) · [wa_security](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html)

#### `T3.2.K6` Patching practices

**要点**：补丁管理：Patch Manager、维护窗口、合规报告

**为什么重要**：「让几百台 EC2 保持补丁合规」是固定题型。

**自测**：
- Patch Baseline 与 Patch Group 的关系？
- 如何对不可变基础设施做补丁？

**文档**：[patch_manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/patch-manager.html)

### D3 · T3.3 Determine a strategy to improve performance

#### `T3.3.K4` Service level agreements (SLAs) and key performance indicators (KPIs)

**要点**：SLA 与 KPI：AWS 服务的可用性承诺如何影响架构选型

**为什么重要**：多层架构的复合可用性计算是典型计算题。

**自测**：
- 两个 99.9% 的服务串联，整体可用性是多少？
- 并联冗余后呢？

**文档**：[sla](https://aws.amazon.com/legal/service-level-agreements/) · [wa_reliability](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)

### D3 · T3.4 Determine a strategy to improve reliability

#### `T3.4.K1` AWS Global Infrastructure

**要点**：从可靠性改进角度看全球基础设施（第四次出现）

**为什么重要**：考纲在四个 task 里重复提这一条，本身就是信号。

**自测**：
- 现有单 Region 架构要提升到什么级别，成本拐点在哪？

**文档**：[global_infra](https://aws.amazon.com/about-aws/global-infrastructure/) · [wa_reliability](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)

#### `T3.4.K2` Data replication methods

**要点**：数据复制方法：同步 vs 异步、S3 CRR/SRR、Aurora 全球库、DynamoDB 全局表

**为什么重要**：复制方式直接决定 RPO，是容灾题的技术底座。

**自测**：
- S3 复制是同步还是异步？RTC 提供什么保证？
- 读副本与多 AZ 备用实例的区别？

**文档**：[s3_replication](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication.html) · [rds_multiaz](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html) · [dr_whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-workloads-on-aws.html)

#### `T3.4.K4` High availability and resiliency

**要点**：高可用与弹性：健康检查、自动扩展、优雅降级、重试与退避

**为什么重要**：「让现有架构更抗故障」类题的答案模式。

**自测**：
- ELB 健康检查失败后会发生什么？与 ASG 健康检查如何联动？
- 断路器模式在 AWS 上怎么实现？

**文档**：[wa_reliability](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)

#### `T3.4.K5` Disaster recovery methods and tools

**要点**：容灾方法与工具：AWS Backup、Elastic Disaster Recovery、跨账户跨区备份

**为什么重要**：与 T2.2.K5 的方案层对应，这里考具体工具。

**自测**：
- AWS Backup 如何做跨账户备份？备份保管库锁定解决什么问题？

**文档**：[backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/whatisbackup.html) · [dr_whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-workloads-on-aws.html)

### D3 · T3.5 Identify opportunities for cost optimizations

#### `T3.5.K1` Cost-conscious architecture choices (for example, using Spot Instances, scaling policies, and rightsizing resources)

**要点**：成本敏感的架构选择：Spot、Savings Plans、预留、Graviton、扩缩容策略

**为什么重要**：「降低成本但不影响可用性」是固定套路题。

**自测**：
- Spot 中断通知有多少秒？如何优雅处理？
- Savings Plans 与 RI 的灵活性差别？

**文档**：[wa_cost](https://docs.aws.amazon.com/wellarchitected/latest/cost-optimization-pillar/welcome.html) · [compute_optimizer](https://docs.aws.amazon.com/compute-optimizer/latest/ug/what-is-compute-optimizer.html)

#### `T3.5.K4` Cost management, alerting, and reporting

**要点**：成本管理与告警：Cost Explorer、CUR、Budgets、成本分配标签、Cost Categories

**为什么重要**：多账户成本分摊题的答案全部来自这里。

**自测**：
- 成本分配标签激活后能回溯多久？
- Cost Categories 解决标签覆盖不全的什么问题？

**文档**：[cost_explorer](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-what-is.html) · [budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html) · [org_services](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_integrate_services.html)

### D4 · T4.1 Select existing workloads and processes for potential migration

#### `T4.1.K3` Asset planning

**要点**：资产盘点与规划：Application Discovery Service、依赖关系发现

**为什么重要**：迁移评估阶段的工具选型。

**自测**：
- Agentless 与 Agent-based 发现分别能拿到什么数据？
- 要画出应用间网络依赖用哪种？

**文档**：[discovery](https://docs.aws.amazon.com/application-discovery/latest/userguide/what-is-appdiscovery.html) · [migration_hub](https://docs.aws.amazon.com/migrationhub/latest/ug/whatis-migrationhub.html)

#### `T4.1.K4` Prioritization and migration of workloads (for example, wave planning)

**要点**：迁移优先级与波次规划（wave planning）、6R 策略

**为什么重要**：大规模迁移题的方法论框架。

**自测**：
- 6R 分别是什么？Replatform 与 Refactor 的界线在哪？
- 波次划分的依据是什么？

**文档**：[six_rs](https://docs.aws.amazon.com/whitepapers/latest/aws-migration-whitepaper/the-6-rs-6-application-migration-strategies.html) · [migration_strategy](https://docs.aws.amazon.com/prescriptive-guidance/latest/strategy-migration/welcome.html) · [migration_hub](https://docs.aws.amazon.com/migrationhub/latest/ug/whatis-migrationhub.html)
