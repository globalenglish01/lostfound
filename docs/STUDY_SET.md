# 最优选题（覆盖优化）

> 由 `python -m scripts.coverage_gaps` 自动生成。

在题池**够得到**的考点范围内，用贪心最大覆盖选出最少的题，
让每个考点被覆盖 k 次。够不到的 36 个考点见 [BLIND_SPOTS.md](BLIND_SPOTS.md)。

| k（每个考点覆盖几次） | 题数 | 覆盖考点 |
|---|---|---|
| 1 | **19** | 57/57 |
| 2 | **24** | 57/57 |
| 3 | **28** | 57/57 |

其中 15 道来自「同一考点的不同问法」配对，**强制保留**：
它们在覆盖意义上是冗余的，但正是训练「换个说法还认不认得」的材料。

## k = 1：19 道

| 题号 | 覆盖考点数 | 涉及服务 |
|---|---|---|
| #187 | 4 | AWS Glue, AWS IoT Core, AWS IoT Greengrass, Amazon Data Firehose |
| #233 | 18 | AWS Backup, AWS CloudFormation, AWS CodePipeline, AWS Database Migration Service |
| #246 | 13 | AWS Budgets, AWS Identity and Access Management, AWS Lambda, AWS Organizations |
| #306 | 17 | AWS Auto Scaling, AWS Lambda, AWS Shield, AWS WAF |
| #329 | 3 | AWS IoT Core, AWS Lambda, AWS Step Functions |
| #345 | 13 | AWS Command Line Interface, AWS Identity and Access Management, AWS Lambda, Amazon Athena |
| #356 | 8 | AWS DataSync, AWS Fargate, Amazon DynamoDB, Amazon ElastiCache |
| #367 | 6 | AWS Identity and Access Management, AWS Security Token Service, Amazon Cognito |
| #423 | 19 | AWS Command Line Interface, AWS Identity and Access Management, AWS Lambda, AWS Trusted Advisor |
| #495 | 4 | AWS Command Line Interface, AWS Direct Connect, AWS VPN, Amazon FSx |
| #498 | 4 | AWS Direct Connect, AWS Elastic Disaster Recovery, AWS VPN, Amazon Virtual Private Cloud |
| #505 | 5 | Amazon Aurora, Amazon Elastic Compute Cloud, Amazon Virtual Private Cloud |
| #51 | 16 | AWS Backup, AWS CloudFormation, AWS Config, AWS Control Tower |
| #510 | 5 | AWS CloudFormation, AWS Lambda, Amazon Route 53 |
| #69 | 7 | AWS Budgets, AWS Cost Explorer, AWS Lambda, AWS Organizations |
| #414 | 22 | AWS Config, AWS Fargate, AWS Lambda, Amazon CloudWatch |
| #545 | 23 | AWS Compute Optimizer, AWS Config, AWS Cost Explorer, AWS Cost and Usage Report |
| #531 | 6 | AWS CloudHSM, AWS Key Management Service, AWS Secrets Manager, Amazon Elastic Compute Cloud |
| #535 | 13 | AWS Application Discovery Service, AWS DataSync, AWS Database Migration Service, AWS Direct Connect |

## k = 2：24 道

| 题号 | 覆盖考点数 | 涉及服务 |
|---|---|---|
| #187 | 4 | AWS Glue, AWS IoT Core, AWS IoT Greengrass, Amazon Data Firehose |
| #233 | 18 | AWS Backup, AWS CloudFormation, AWS CodePipeline, AWS Database Migration Service |
| #246 | 13 | AWS Budgets, AWS Identity and Access Management, AWS Lambda, AWS Organizations |
| #306 | 17 | AWS Auto Scaling, AWS Lambda, AWS Shield, AWS WAF |
| #329 | 3 | AWS IoT Core, AWS Lambda, AWS Step Functions |
| #345 | 13 | AWS Command Line Interface, AWS Identity and Access Management, AWS Lambda, Amazon Athena |
| #356 | 8 | AWS DataSync, AWS Fargate, Amazon DynamoDB, Amazon ElastiCache |
| #367 | 6 | AWS Identity and Access Management, AWS Security Token Service, Amazon Cognito |
| #423 | 19 | AWS Command Line Interface, AWS Identity and Access Management, AWS Lambda, AWS Trusted Advisor |
| #495 | 4 | AWS Command Line Interface, AWS Direct Connect, AWS VPN, Amazon FSx |
| #498 | 4 | AWS Direct Connect, AWS Elastic Disaster Recovery, AWS VPN, Amazon Virtual Private Cloud |
| #505 | 5 | Amazon Aurora, Amazon Elastic Compute Cloud, Amazon Virtual Private Cloud |
| #51 | 16 | AWS Backup, AWS CloudFormation, AWS Config, AWS Control Tower |
| #510 | 5 | AWS CloudFormation, AWS Lambda, Amazon Route 53 |
| #69 | 7 | AWS Budgets, AWS Cost Explorer, AWS Lambda, AWS Organizations |
| #384 | 25 | AWS CloudFormation, AWS Config, AWS Identity and Access Management, AWS Organizations |
| #534 | 15 | AWS Auto Scaling, Amazon DynamoDB, Amazon EC2 Auto Scaling, Amazon ElastiCache |
| #545 | 23 | AWS Compute Optimizer, AWS Config, AWS Cost Explorer, AWS Cost and Usage Report |
| #50 | 22 | AWS Backup, AWS Command Line Interface, AWS DataSync, AWS Database Migration Service |
| #547 | 8 | AWS Fargate, AWS Lambda, Amazon Elastic Compute Cloud, Savings Plans |
| #544 | 11 | AWS Lambda, Amazon CloudWatch, Amazon Elastic Block Store, Amazon Elastic Compute Cloud |
| #311 | 12 | AWS Application Migration Service, AWS DataSync, AWS Database Migration Service, AWS Direct Connect |
| #414 | 22 | AWS Config, AWS Fargate, AWS Lambda, Amazon CloudWatch |
| #535 | 13 | AWS Application Discovery Service, AWS DataSync, AWS Database Migration Service, AWS Direct Connect |

## k = 3：28 道

| 题号 | 覆盖考点数 | 涉及服务 |
|---|---|---|
| #187 | 4 | AWS Glue, AWS IoT Core, AWS IoT Greengrass, Amazon Data Firehose |
| #233 | 18 | AWS Backup, AWS CloudFormation, AWS CodePipeline, AWS Database Migration Service |
| #246 | 13 | AWS Budgets, AWS Identity and Access Management, AWS Lambda, AWS Organizations |
| #306 | 17 | AWS Auto Scaling, AWS Lambda, AWS Shield, AWS WAF |
| #329 | 3 | AWS IoT Core, AWS Lambda, AWS Step Functions |
| #345 | 13 | AWS Command Line Interface, AWS Identity and Access Management, AWS Lambda, Amazon Athena |
| #356 | 8 | AWS DataSync, AWS Fargate, Amazon DynamoDB, Amazon ElastiCache |
| #367 | 6 | AWS Identity and Access Management, AWS Security Token Service, Amazon Cognito |
| #423 | 19 | AWS Command Line Interface, AWS Identity and Access Management, AWS Lambda, AWS Trusted Advisor |
| #495 | 4 | AWS Command Line Interface, AWS Direct Connect, AWS VPN, Amazon FSx |
| #498 | 4 | AWS Direct Connect, AWS Elastic Disaster Recovery, AWS VPN, Amazon Virtual Private Cloud |
| #505 | 5 | Amazon Aurora, Amazon Elastic Compute Cloud, Amazon Virtual Private Cloud |
| #51 | 16 | AWS Backup, AWS CloudFormation, AWS Config, AWS Control Tower |
| #510 | 5 | AWS CloudFormation, AWS Lambda, Amazon Route 53 |
| #69 | 7 | AWS Budgets, AWS Cost Explorer, AWS Lambda, AWS Organizations |
| #414 | 22 | AWS Config, AWS Fargate, AWS Lambda, Amazon CloudWatch |
| #384 | 25 | AWS CloudFormation, AWS Config, AWS Identity and Access Management, AWS Organizations |
| #534 | 15 | AWS Auto Scaling, Amazon DynamoDB, Amazon EC2 Auto Scaling, Amazon ElastiCache |
| #50 | 22 | AWS Backup, AWS Command Line Interface, AWS DataSync, AWS Database Migration Service |
| #83 | 19 | AWS Auto Scaling, AWS DataSync, AWS Database Migration Service, AWS Lambda |
| #545 | 23 | AWS Compute Optimizer, AWS Config, AWS Cost Explorer, AWS Cost and Usage Report |
| #547 | 8 | AWS Fargate, AWS Lambda, Amazon Elastic Compute Cloud, Savings Plans |
| #549 | 19 | AWS CloudTrail, AWS Config, AWS Identity and Access Management, AWS Key Management Service |
| #20 | 10 | AWS Fargate, AWS Lambda, AWS Organizations, Amazon Elastic Compute Cloud |
| #544 | 11 | AWS Lambda, Amazon CloudWatch, Amazon Elastic Block Store, Amazon Elastic Compute Cloud |
| #311 | 12 | AWS Application Migration Service, AWS DataSync, AWS Database Migration Service, AWS Direct Connect |
| #344 | 17 | AWS Application Discovery Service, AWS Backup, AWS CloudFormation, AWS DataSync |
| #535 | 13 | AWS Application Discovery Service, AWS DataSync, AWS Database Migration Service, AWS Direct Connect |
