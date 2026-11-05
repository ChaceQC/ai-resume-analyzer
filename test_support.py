"""离线测试的模拟模型输出及合成校准材料，不是真实候选人的业绩。"""

from rubric import criteria


WEAK_RESUME = """王同学
电话：13800138000 邮箱：test@example.com
教育背景
2020—2024 示例大学 本科
专业技能
熟悉 Python、Java、C++、SQL、MySQL、Redis、Docker、Kubernetes、Linux、Git、React、Vue。
熟悉自动化测试、CI/CD、分布式系统、系统设计、大模型与机器学习。
项目经历
2024—2025 业务系统
负责业务系统开发和维护，参与多个项目，完成领导安排的各项工作。
自我评价
认真负责，积极主动，沟通能力好，热爱学习，具有团队协作精神。
"""

VERIFIED_RESUME = """李同学
电话：13800138000 邮箱：li@example.com
教育背景
2020.09—2024.06 示例大学 软件工程 本科
专业技能
Python用于接口与数据校验，MySQL用于交易存储及索引优化，Redis用于热点缓存；使用Docker部署服务并用pytest执行回归。
工作经历
2024.07—2026.10 示例公司 后端工程师
个人负责订单查询链路与缓存一致性方案，业务场景为日均8万次订单查询，负责方案设计、实现及上线验证。
独立使用Python实现查询接口，对比本地缓存与Redis共享缓存后选择后者，以统一多实例失效策略；通过订单版本号和消息重试避免旧值覆盖，保留设计评审记录。
在相同4核8GB容器、同一20万条脱敏订单数据、100并发、预热10分钟后持续压测30分钟的条件下，P95从320ms降至115ms；错误率从0.8%降至0.05%，压测脚本和原始报告由测试同事复核。
个人主导MySQL慢查询治理，针对订单状态和创建时间设计复合索引；使用EXPLAIN验证执行计划，因写入开销放弃为所有字段建立索引。
在相同数据库实例、同一百万行数据及固定查询集合上，各执行5轮查询并报告中位数，报表耗时由8秒降至2秒；上线后连续14天监控，未观察到写入P95恶化，复核报告和监控面板均有归档。
本人负责Python对账校验模块，以幂等键和分批提交替代全量事务，处理重复导入和中途失败的恢复；明确不负责上游业务录入。
对同一批500份历史账单进行新旧流程对照，人工复核差异项，单批处理时间由2小时降至25分钟；异常检出率从92%提高到99%，统计分母及差异清单由财务同事签字验收。
独立编写并执行pytest回归，新增80个覆盖重复提交、超时重试和权限边界的用例；CI连续20次构建均通过，覆盖率由61%升至86%，失败日志和构建记录可追溯。
使用Docker实现可回滚发布，通过小流量灰度和错误率阈值控制上线；连续观察30天未发现重复扣款，生产事件记录由值班同事交叉核对。
"""


def model_assessment(text, job="", market_items=()):
    """构造合法的模拟响应；分值仅用于验证协议，不代表材料质量。"""
    quote = text[:180]
    return {
        "summary": "这是模拟模型按评分细则给出的评估，用于接口及原文校验测试。",
        "score_items": [{"id": item["id"], "points": item["max_points"] // 2,
                         "reason": "模拟模型对本项证据作出判断，并指出仍需补充的细节。",
                         "evidence": [quote] if item["max_points"] // 2 else []} for item in criteria()],
        "requirements": ([{"title": "对照岗位要求", "priority": "必备", "job_quote": job,
                           "skills": ["Python"], "match": "partial", "reason": "有部分对应依据。", "evidence": [quote]}] if job else []),
        "market_items": [{"id": item["id"], "match": "stated", "reason": "模拟判断为仅有陈述。", "evidence": [quote]} for item in market_items],
        "strengths": ["已有基础经历描述。"],
        "suggestions": [{"priority": "高", "title": "补充验证依据", "detail": "说明结果的测量条件及个人贡献。"}],
        "rewrites": [], "interview_questions": ["如何验证结果和个人贡献？"],
    }
