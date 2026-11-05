"""固定规则、原文证据和岗位条件。此模块不调用模型或网络。"""

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP


VERSION = "3.0.1"
DIMENSIONS = {"信息完整度": 10, "内容结构": 15, "技能呈现": 25, "成果表达": 35, "表达清晰度": 15}
SECTIONS = {
    "education": r"教育背景|教育经历|学历|教育|education(?:al background)?|qualifications",
    "skills": r"专业技能|技术技能|技术栈|技能|核心能力|technical skills|skills|competencies",
    "experience": r"项目或工作经历|项目经历|相关实践|实践经历|研究经历|工作经历|工作经验|实习经历|项目经验|科研经历|项目|professional experience|work experience|employment history|work history|experience|projects?|internships?|research experience",
    "other": r"自我评价|个人评价|兴趣爱好|求职意向|个人信息|联系方式|summary|interests|references",
}
ACTION = r"负责|主导|独立|带领|编制|对账|签约|调研|构建|改进|改善|设计|开发|实现|优化|搭建|部署|交付|维护|排查|解决|重构|编写|分析|制定|策划|执行|推动|完成|提升|提高|降低|缩短|减少|组织|协调|审计|核算|采购|谈判|诊治|研究|(?<![a-z])(?:built|developed|designed|implemented|optimized|reduced|increased|delivered|led|managed|created|automated|deployed|resolved|improved|maintained|launched|analyzed|audited|negotiated|coordinated|researched)(?![a-z])"
METHOD = r"使用|通过|基于|采用|利用|借助|运用|引入|重构|缓存|索引|对照实验|(?<![a-z])(?:using|with|via|through|by|based on)(?![a-z])"
RESULT = r"提升|提高|降低|减少|缩短|增长|节省|节约|达到|支撑|覆盖|上线|交付|完成|测试|转化|营收|收入|利润|成本|验收|反馈|合规|(?<![a-z])(?:reduced|increased|improved|saved|achieved|delivered|served|supported|processed|tests?|launched|accepted|feedback)(?![a-z])"
METRIC = r"\d+(?:\.\d+)?\s*(?:%|ms\b|秒|分钟|小时|倍|万元|亿元|元|万人|万条|万次|人|名|条|次|项|个|users?\b|requests?\b|tests?\b|hours?\b|minutes?\b|transactions?\b|[qm]ps\b)"
VERIFY = r"测试|验收|审计|实验|回访|反馈|自动化|监控|对照|压测|(?<![a-z])(?:test|tests|testing|audit|experiment|feedback|benchmark|monitoring|validation)(?![a-z])"
CONDITIONS = r"并发|压测|测试环境|测试条件|样本量|对照组|基线|P9[59]|验收记录|审计记录|load test|test environment|control group|sample size|baseline|benchmark"
DATE = r"(?<![\d.])(?:19|20)\d{2}(?=$|[\s./年—–\-~至)\]])(?:[./年-]\d{1,2})?"
NEGATIVE = r"(?:尚未|从未|未曾|没有|缺乏|不具备|不熟悉|不会|不懂|不了解|未掌握|未使用|不要求|无需|无须|无)(?:[^,，;；。\n]{0,18})$|(?:not familiar with|no experience (?:with|in)|never used|without|not required)[^,;\n]{0,18}$"
FUTURE = r"(?:计划|准备|希望|打算|学习目标|拟)(?:[^,，;；。\n]{0,12})$|(?:plan to|want to|planning to)[^,;\n]{0,18}$"

SKILLS = {
    "Python": r"python", "Java": r"java", "C++": r"c\+\+", "C#": r"c#",
    "JavaScript": r"javascript|js", "TypeScript": r"typescript|ts", "Go": r"golang|go",
    "Rust": r"rust", "PHP": r"php", "Flask": r"flask", "FastAPI": r"fastapi", "Django": r"django",
    "Spring Boot": r"spring\s*boot|springboot", "Vue": r"vue(?:\.js)?(?:[23])?", "React": r"react(?:\.js)?",
    "Node.js": r"node[.\s]?js", "HTML": r"html5?", "CSS": r"css3?", "SQL": r"sql",
    "MySQL": r"mysql", "PostgreSQL": r"postgresql|postgres", "Redis": r"redis", "MongoDB": r"mongodb",
    "Docker": r"docker", "Kubernetes": r"kubernetes|k8s", "Linux": r"linux", "Git": r"git",
    "CI/CD": r"ci\s*/\s*cd|持续集成|持续交付", "REST API": r"rest(?:ful)?\s*(?:api)?",
    "自动化测试": r"自动化测试|pytest|unittest|automated test(?:ing|s)?", "消息队列": r"消息队列|rabbitmq|kafka|rocketmq",
    "Nginx": r"nginx", "机器学习": r"机器学习|machine learning", "深度学习": r"深度学习|deep learning",
    "PyTorch": r"pytorch", "TensorFlow": r"tensorflow", "Pandas": r"pandas", "NumPy": r"numpy",
    "数据分析": r"数据分析|data analysis|data analytics", "Excel": r"excel", "Power BI": r"power\s*bi", "Tableau": r"tableau",
    "Figma": r"figma", "Axure": r"axure", "用户研究": r"用户研究|用户调研|user research",
    "需求分析": r"需求分析|requirements analysis", "项目管理": r"项目管理|project management",
    "SEO": r"seo", "A/B 测试": r"a\s*/\s*b\s*(?:测试|test(?:ing)?)",
    "大模型": r"大模型|llm", "RAG": r"rag|检索增强", "LangChain": r"langchain",
    "财务分析": r"财务分析|financial analysis", "预算管理": r"预算管理|budgeting|budget management",
    "财务报告": r"财务报告|financial reporting", "会计核算": r"会计核算|accounting|bookkeeping",
    "审计": r"审计|auditing|audit", "风险管理": r"风险管理|risk management",
    "市场营销": r"市场营销|marketing", "内容运营": r"内容运营|content marketing",
    "用户增长": r"用户增长|user growth|growth marketing", "招聘": r"招聘|recruitment|recruiting",
    "交互设计": r"交互设计|interaction design", "客户管理": r"客户管理|crm|customer relationship management",
    "销售管理": r"销售管理|sales management|account management", "客户成功": r"客户成功|customer success",
    "供应链": r"供应链|supply chain", "采购": r"采购|procurement|purchasing",
    "质量管理": r"质量管理|quality assurance|quality management", "工艺设计": r"工艺设计|manufacturing process",
    "CAD": r"autocad|cad|solidworks", "临床研究": r"临床研究|clinical research|clinical trials",
    "合规": r"合规|compliance", "数据建模": r"数据建模|data modeling|data modelling",
    "分布式系统": r"分布式系统|distributed systems?", "系统设计": r"系统设计|system design|systems design",
    "可观测性": r"可观测性|observability|prometheus|grafana", "云平台": r"云平台|aws|azure|google cloud|gcp",
    "信息安全": r"信息安全|cybersecurity|information security", "团队管理": r"团队管理|people management|team leadership",
    "CPA": r"cpa|注册会计师", "CFA": r"cfa|特许金融分析师", "ACCA": r"acca",
    "执业资格": r"执业资格|执业证书|professional licen[cs]e|medical licen[cs]e",
}
DEGREES = [(4, "博士", r"博士|\bph\.?d\b|doctorate"), (3, "硕士", r"硕士|研究生|master(?:'s)?(?: degree| of )|\bm\.?sc\b"),
           (2, "本科", r"本科|学士|bachelor|\bb\.?sc\b"), (1, "专科", r"大专|专科|associate.degree")]

ROLES = {
    "software": ("软件工程", r"后端|前端|软件|全栈|开发工程|客户端|服务端|测试工程|运维|software|backend|front.?end|full.?stack|platform engineer|site reliability"),
    "data": ("数据与算法", r"数据|算法|机器学习|data |machine learning|research scientist|analytics"),
    "product": ("产品与运营", r"产品|运营|product |operations|program manager"),
    "sales": ("市场销售与客户", r"销售|市场|客户|sales|account executive|marketing|customer success|business development"),
    "finance": ("财务审计与风控", r"财务|会计|审计|风控|finance|financial|accountant|accounting|audit|risk analyst"),
    "design": ("设计与内容", r"设计|内容|design|content|writer"),
    "engineering": ("制造工程与供应链", r"制造|机械|供应链|采购|工艺|manufactur|mechanical|supply chain|procurement|hardware|electrical"),
    "healthcare": ("医疗与生命科学", r"临床|医疗|生物|clinical|medical|nurse|biolog|physician"),
    "people": ("人力与组织管理", r"人力|招聘|人事|human resources|recruit|people partner"),
}
INDUSTRIES = {"software": "软件与互联网", "finance": "金融科技", "healthcare": "医疗健康", "engineering": "制造与工程"}
LEVELS = {"graduate": "校招与实习", "junior": "初中级", "senior": "资深与专家", "manager": "管理岗位"}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def rounded(value) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def positive_mentions(text: str) -> list[dict]:
    value = normalize(text)
    found = []
    for name, pattern in SKILLS.items():
        for match in re.finditer(r"(?<![a-z0-9_])(?:" + pattern + r")(?![a-z0-9_])", value, re.I):
            prefix = re.split(r"[,，;；。!！?？\n]|但是|但|\bbut\b|\bhowever\b", value[:match.start()], flags=re.I)[-1][-70:]
            prefix = re.sub(r"不但|不仅|not only", "", prefix, flags=re.I)
            suffix = re.split(r"[,，;；。!！?？\n]|但是|但|\bbut\b", value[match.end():], flags=re.I)[0][:70]
            if (re.search(NEGATIVE + "|" + FUTURE, prefix, re.I)
                    or re.match(r"\s*(?:不熟悉|不会|尚未掌握|经验不足)", suffix)
                    or re.match(r"\s*(?:(?:is|are)\s+)?not (?:required|needed|necessary|familiar)", suffix, re.I)
                    or re.search(r"(?:都|均)(?:不会|不熟悉|未使用|无经验)", suffix)):
                continue
            found.append({"skill": name, "start": match.start(), "end": match.end()})
    return sorted(found, key=lambda item: (item["start"], item["skill"]))


def extract_skills(text: str) -> list[str]:
    names = {item["skill"] for item in positive_mentions(text)}
    return [name for name in SKILLS if name in names]


def experience_years(value: str) -> float:
    chinese = {name: index for index, name in enumerate("零一二三四五六七八九十")}
    match = re.search(r"(?<!\d)([一二三四五六七八九十\d]{1,2}(?:\.\d+)?)\s*\+?\s*年(?:以上)?[^，。;；\n]{0,18}(?:经验|工作)|(?<!\d)(\d+(?:\.\d+)?)\s*\+?\s*years?[^,.;\n]{0,35}experience", value, re.I)
    if not match:
        return 0
    prefix = re.split(r"[,，;；。\n]|但是|但|\bbut\b", value[:match.start()], flags=re.I)[-1]
    if re.search(NEGATIVE + "|" + FUTURE + r"|(?:未达到|不足|少于)\s*$", prefix, re.I):
        return 0
    number = match.group(1) or match.group(2)
    return float(chinese[number]) if number in chinese else (float(number) if re.fullmatch(r"\d+(?:\.\d+)?", number) else 0)


def degree_claim(value: str, minimum: int, allow_enrolled=False) -> bool:
    for rank, _, pattern in DEGREES:
        if rank < minimum:
            continue
        for match in re.finditer(pattern, value, re.I):
            prefix = re.split(r"[,，;；。\n]|但是|但|\bbut\b", value[:match.start()], flags=re.I)[-1]
            suffix = re.split(r"[,，;；。\n]", value[match.end():])[0][:50]
            if re.search(NEGATIVE + "|" + FUTURE + r"|(?:未取得|尚未取得)\s*$", prefix, re.I):
                continue
            if not allow_enrolled and re.search(r"在读|未毕业|in progress|candidate|currently enrolled", suffix, re.I):
                continue
            return True
    return False


def source_units(text: str, source="resume") -> list[dict]:
    """保留原文坐标，合并软换行；标题、项目时间和新行动另起记录。"""
    normal, positions = [], []
    for index, char in enumerate(text):
        if char == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            continue
        for converted in unicodedata.normalize("NFKC", char):
            normal.append(converted if converted != "\r" else "\n")
            positions.append(index)
    value = "".join(normal)
    records, pending, section, offset = [], None, "", 0

    def flush():
        nonlocal pending
        if pending is None:
            return
        start, end, family = pending
        for match in re.finditer(r"[^。；;!?！？]+(?:[。；;!?！？]|$)", value[start:end]):
            left, right = start + match.start(), start + match.end()
            while left < right and value[left].isspace():
                left += 1
            while right > left and value[right - 1].isspace():
                right -= 1
            if left < right:
                raw_start, raw_end = positions[left], positions[right - 1] + 1
                quote = text[raw_start:raw_end]
                records.append({"id": fingerprint([source, re.sub(r"\s+", "", value[left:right]).casefold()])[:16],
                                "source": source, "start": raw_start, "end": raw_end, "quote": quote,
                                "text": re.sub(r"\s+", " ", value[left:right]).strip(), "section": family})
        pending = None

    for line in value.splitlines(keepends=True):
        start, end = offset, offset + len(line.rstrip("\n"))
        offset += len(line)
        stripped = re.sub(r"^[\s#•·*\-]+|\s+$", "", line)
        bullet = re.match(r"(?:\d{1,2}[.)、]|[一二三四五六七八九十][、.)])\s*", stripped)
        if bullet:
            stripped = stripped[bullet.end():]
        if not stripped:
            flush()
            continue
        start = value.find(stripped, start, offset)
        heading = None
        for family, pattern in SECTIONS.items():
            heading = re.match(r"[【\[]?(?:" + pattern + r")(?=\s|[:：|】\]]|$)[】\]]?\s*[:：|]?\s*", stripped, re.I)
            if heading:
                flush()
                section = family
                start += heading.end()
                stripped = stripped[heading.end():]
                break
        if not stripped.strip():
            continue
        boundary = (heading or bullet or re.match(DATE, stripped) or re.match(ACTION + "|" + METHOD, stripped, re.I)
                    or re.search(r"@|电话[:：]|邮箱[:：]", stripped)
                    or (source == "job" and pending and not re.search(r"(?:或|\bor)\s*$", value[pending[0]:pending[1]], re.I)))
        if pending and boundary:
            flush()
        pending = (pending[0] if pending else start, end, section)
    flush()
    return records


def citation(unit: dict) -> dict:
    return {key: unit[key] for key in ("id", "source", "start", "end", "quote")}


def personal_clauses(value: str) -> list[str]:
    """团队陈述不能自动归为个人能力；显式切换到本人职责后恢复收集。"""
    selected, team = [], False
    for clause in re.split(r"[,，]", value):
        if re.search(r"团队|\bteam\b|\bwe\b", clause, re.I):
            team = True
        if re.search(r"本人|我负责|我主导|\bI\b|\bmy (?:role|responsib|contribution)", clause, re.I) or ("独立" in clause and not re.search(r"团队|\bteam\b", clause, re.I)):
            team = False
        if not team:
            selected.append(clause)
    return selected


def candidate_skills(text: str, units=None) -> list[str]:
    units = source_units(text) if units is None else units
    names = {name for unit in units for name in extract_skills("，".join(personal_clauses(unit["text"])))}
    return [name for name in SKILLS if name in names]


def facts(text: str) -> dict:
    units = source_units(text)
    seen, actions, methods, outcomes, owned, verified, conditions, comparisons = set(), [], [], [], [], [], [], []
    practiced = {}
    for unit in units:
        value = "，".join(personal_clauses(unit["text"]))
        skills = extract_skills(value)
        action_value = re.sub(r"(?:开发|研发|设计|测试|分析|采购|研究)(?:工程师|实习生|专员|经理|方向|专业|课程|师|员)|负责人|认真负责|积极负责", "", value)
        action = bool(re.search(ACTION, action_value, re.I))
        action = action and not re.search(r"^(?:计划|希望|打算|准备学习|未能|尚未|没有|planning to|want to)", value, re.I)
        action = action and not (re.match(DATE, value) and not re.search(METHOD + "|" + METRIC, value, re.I))
        if action and len(re.sub(r"\s+", "", value)) >= 10:
            for skill in skills:
                practiced.setdefault(skill, unit)
        if unit["section"] in {"education", "skills", "other"} or not action or unit["id"] in seen:
            continue
        if len(re.sub(r"\s+", "", value)) < 10:
            continue
        seen.add(unit["id"])
        actions.append(unit)
        if re.search(METHOD, value, re.I):
            methods.append(unit)
        team_result = bool(re.search(r"团队|\bteam\b|\bwe\b", value, re.I)) and not re.search(r"本人|我负责|我主导|带领|独立|\bI (?:led|owned|implemented)\b", value, re.I)
        if not team_result and re.search(RESULT, value, re.I) and (re.search(METRIC, value, re.I) or re.search(r"上线|交付|验收|反馈|合规|launched|delivered|accepted|feedback", value, re.I)):
            outcomes.append(unit)
        ownership_clauses = re.split(r"[,，]", value)
        if any(re.search(r"负责|主导|独立|带领|\b(?:led|owned|independently)\b", clause, re.I)
               and not re.search(r"团队负责|团队主导|团队独立|团队带领|team led|team owned", clause, re.I) for clause in ownership_clauses):
            owned.append(unit)
        if re.search(VERIFY, value, re.I):
            verified.append(unit)
        if re.search(CONDITIONS, value, re.I):
            conditions.append(unit)
        if not team_result and re.search(METRIC, value, re.I) and re.search(r"(?:从|由).{0,60}(?:降|提|缩|增|到|至)|\bfrom\b.{0,60}\bto\b", value, re.I):
            comparisons.append(unit)
    return {"units": units, "actions": actions, "methods": methods, "outcomes": outcomes, "owned": owned,
            "verified": verified, "conditions": conditions, "comparisons": comparisons, "practiced": practiced}


def parse_requirements(job: str) -> list[dict]:
    requirements = {}
    optional_section = False
    for unit in source_units(job, "job"):
        value = unit["text"]
        if re.search(r"加分项|优先条件|nice to have|preferred qualifications|bonus points", value, re.I):
            optional_section = True
        if re.search(r"任职要求|必须条件|required qualifications|minimum qualifications", value, re.I):
            optional_section = False
        # 逗号通常分隔独立要求；or / 或连接的能力属于同一要求。
        for clause in re.split(r"[,，。;；\n]", value):
            mentions = positive_mentions(clause)
            groups = []
            for mention in mentions:
                if groups and re.search(r"或|或者|\bor\b|/", clause[groups[-1][-1]["end"]:mention["start"]], re.I):
                    groups[-1].append(mention)
                else:
                    groups.append([mention])
            for group in groups:
                names = sorted({item["skill"] for item in group})
                key = "|".join(names)
                optional = optional_section or bool(re.search(r"优先|加分|preferred|bonus|nice.to.have|a plus", clause, re.I))
                practical = bool(re.search(r"实践|实际|项目经验|工作经验|熟练|hands.on|experience (?:with|in)|proficien", clause, re.I)
                                 or re.search(r"其中(?:一种|一项|之一).{0,20}(?:经验|实践)", value))
                record = {"id": "req-" + fingerprint(names)[:12], "skills": names, "priority": "加分" if optional else "必备",
                          "weight": 1 if optional else 3, "practical": practical, "evidence": [citation(unit)]}
                if key not in requirements or record["weight"] > requirements[key]["weight"]:
                    requirements[key] = record
            optional = optional_section or bool(re.search(r"优先|加分|preferred|bonus|nice.to.have|a plus", clause, re.I))
            qualifiers = []
            if not re.search(r"不限学历|学历不限|不要求学历|no degree required", clause, re.I):
                qualifiers.extend(("degree", rank, label + "或以上学历") for rank, label, pattern in DEGREES if re.search(pattern, clause, re.I))
            years = experience_years(clause)
            if years:
                qualifiers.append(("years", years, f"明确的相关经验至少{years:g}年"))
            for kind, minimum, title in qualifiers:
                key = f"{kind}:{minimum}"
                record = {"id": "req-" + fingerprint(key)[:12], "kind": kind, "minimum": minimum, "title": title,
                          "skills": [], "priority": "加分" if optional else "必备", "weight": 1 if optional else 3,
                          "practical": False, "allow_enrolled": bool(re.search(r"在读|在校|pursuing|currently enrolled", clause, re.I)), "evidence": [citation(unit)]}
                if key not in requirements or record["weight"] > requirements[key]["weight"]:
                    requirements[key] = record
    return sorted(requirements.values(), key=lambda item: item["id"])


def requirement_items(text: str, job: str, evidence: dict) -> list[dict]:
    skills = set(candidate_skills(text, evidence["units"]))
    items = parse_requirements(job)
    total_weight = sum(item["weight"] for item in items)
    remaining = Decimal("100.00")
    for index, item in enumerate(items):
        maximum = (Decimal(100) * item["weight"] / total_weight).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        if index == len(items) - 1:
            maximum = remaining
        remaining -= maximum
        matched = [name for name in item["skills"] if name in skills]
        practiced = [name for name in matched if name in evidence["practiced"]]
        ratio = Decimal(1) if matched else Decimal(0)
        resume_supports = []
        if item.get("kind") == "degree":
            resume_supports = [unit for unit in evidence["units"] if degree_claim("，".join(personal_clauses(unit["text"])), item["minimum"], item["allow_enrolled"])]
        elif item.get("kind") == "years":
            resume_supports = [unit for unit in evidence["units"] if unit["section"] != "education" and not re.search(r"课程|在校|学习|student|coursework", unit["text"], re.I) and experience_years("，".join(personal_clauses(unit["text"]))) >= item["minimum"]]
        if item.get("kind"):
            ratio = Decimal(1) if resume_supports else Decimal(0)
            matched = [item["title"]] if resume_supports else []
        if matched and item["practical"] and not practiced:
            ratio = Decimal(".5")
        item.update({"title": item.get("title") or " 或 ".join(item["skills"]), "max_points": float(maximum),
                     "points": float((maximum * ratio).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)),
                     "matched": matched, "status": ("已提供对应陈述" if matched else "未提供明确依据") if item.get("kind") else ("已提供实践" if practiced else ("仅有技能陈述" if matched else "未提供证据")),
                     "rule": ("按明确学历或经验年限陈述对照岗位门槛；年限不从项目数量或教育日期推断。必备项权重3，加分项权重1。" if item.get("kind") else "可替代技能满足其一即可；必备项权重3，加分项权重1；明确要求实践时，仅列技能得本项一半。"),
                     "resume_evidence": [citation(unit) for unit in resume_supports] or [citation(evidence["practiced"][name]) for name in practiced] or
                                        [citation(unit) for unit in evidence["units"] if any(name in candidate_skills("", [unit]) for name in matched)][:2]})
    return items


def resolve_context(job: str, context=None) -> dict:
    result = {"industry": "", "role": "", "level": "", "city": ""}
    result.update(context or {})
    if not result["role"]:
        title = normalize(job).splitlines()[0] if job.strip() else ""
        result["role"] = next((key for key, (_, pattern) in ROLES.items() if re.search(pattern, title, re.I)), "")
    if not result["industry"]:
        result["industry"] = next((key for key, pattern in {"finance": r"金融|银行|支付|fintech|banking|payments", "healthcare": r"医疗|医药|临床|healthcare|medical", "engineering": r"制造|供应链|汽车|manufacturing|automotive"}.items() if re.search(pattern, job, re.I)), "")
    return result


def evaluate(text: str, job: str, context=None) -> dict:
    context = resolve_context(job, context)
    evidence = facts(text)
    units = evidence["units"]
    skills = candidate_skills(text, units)
    normal = normalize(text)
    email = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", normal)
    phone = re.search(r"(?<!\d)(?:\+86[ -]?)?1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)", normal)
    if not phone:
        phone = next((match for match in re.finditer(r"\+\d[\d ()-]{6,}\d", normal) if 7 <= len(re.sub(r"\D", "", match.group())) <= 15), None)
    degree = next((label for label, pattern in [("博士", r"博士|\bph\.?d\b|doctorate"), ("硕士", r"硕士|研究生|master|\bm\.?sc\b"), ("本科", r"本科|学士|bachelor|\bb\.?sc\b"), ("专科", r"大专|专科|associate.degree")] if re.search(pattern, normal, re.I)), "未识别")
    first = next((line.strip() for line in normal.splitlines() if line.strip() and line.strip().casefold() not in {"简历", "个人简历", "resume", "curriculum vitae"}), "")
    name = re.sub(r"^(?:姓名|name)\s*[:：]?\s*", "", first, flags=re.I)
    has_name = bool(name) and len(name) <= 40 and not re.search(r"[@\d:：]|电话|邮箱|phone|email", name, re.I)
    education = [unit for unit in units if re.search(r"大学|学院|本科|硕士|博士|专科|university|college|bachelor|master|doctorate", unit["text"], re.I)]
    skill_units = [unit for unit in units if candidate_skills("", [unit])]
    experience = evidence["actions"]
    chronology = [unit for unit in units if unit["section"] == "experience" and re.search(DATE, unit["text"])]
    items = []

    def add(identifier, dimension, title, maximum, points, rule, supports=(), missing="补充真实且具体的相关描述。"):
        items.append({"id": identifier, "dimension": dimension, "title": title, "max_points": maximum,
                      "points": max(0, min(maximum, points)), "rule": rule, "rule_version": VERSION,
                      "evidence": [citation(unit) for unit in supports][:5], "missing": missing if points < maximum else ""})

    contact_email = [unit for unit in units if email and email.group() in normalize(unit["quote"])]
    contact_phone = [unit for unit in units if phone and phone.group() in normalize(unit["quote"])]
    add("info.email", "信息完整度", "邮箱", 4, 4 if email else 0, "提供可识别的邮箱得4分。", contact_email, "补充邮箱。")
    add("info.phone", "信息完整度", "联系电话", 3, 3 if phone else 0, "提供可识别的国内或国际电话得3分。", contact_phone, "补充联系电话。")
    add("info.name", "信息完整度", "姓名信息", 1, int(has_name), "提供可识别的姓名得1分，不按姓名内容评价。", units[:1] if has_name else [], "在顶部明确列出姓名。")
    add("info.education", "信息完整度", "教育或资格背景", 2, 2 if education else 0, "提供教育或资格背景得2分，不按学校或学历层级加分。", education, "补充教育或资格背景。")
    add("structure.education", "内容结构", "教育信息可定位", 4, 4 if education else 0, "能定位到教育正文得4分；仅标题不计分。", education)
    add("structure.skills", "内容结构", "专业能力可定位", 4, 4 if skill_units else 0, "能定位到专业能力陈述得4分；标题名称不影响得分。", skill_units)
    add("structure.experience", "内容结构", "实践内容可定位", 5, 5 if experience else 0, "有具体行动描述得5分；支持项目、工作、研究与课程实践。", experience)
    add("structure.timeline", "内容结构", "经历时间", 2, 2 if chronology else 0, "经历中有可识别的年份信息得2分；日期不计为成果。", chronology, "为经历补充时间范围。")
    add("skills.stated", "技能呈现", "专业能力陈述", 5, min(5, len(skills)), "每项正向技能陈述1分，最多5分；否定、计划和重复不计。", skill_units)
    practiced_units = list({unit["id"]: unit for unit in evidence["practiced"].values()}.values())
    add("skills.applied", "技能呈现", "能力实际应用", 10, min(10, len(evidence["practiced"]) * 2), "每项在具体行动中出现的能力2分，最多10分；增加无关技能不降低此项。", practiced_units, "说明能力用于什么实际任务。")
    add("skills.method", "技能呈现", "实现方法", 5, min(5, len(evidence["methods"]) * 2), "每条不同的方法描述2分，最多5分。", evidence["methods"], "补充采用的方法或技术措施。")
    add("skills.verify", "技能呈现", "验证与反馈", 5, min(5, len(evidence["verified"]) * 3), "每条测试、验收、审计或反馈记录3分，最多5分。", evidence["verified"], "说明如何验证交付质量。")
    expected = 2 if context["level"] == "graduate" else (4 if context["level"] in {"senior", "manager"} else 3)
    add("results.action", "成果表达", "具体行动", 8, min(8, 8 * len(experience) // expected), f"{expected}条不同的具体行动可得8分，按条数比例取整；重复描述不计。", experience)
    expected_owned = 2 if context["level"] in {"senior", "manager"} else 1
    add("results.ownership", "成果表达", "个人负责范围", 7, min(7, 7 * len(evidence["owned"]) // expected_owned), f"{expected_owned}条明确个人负责、主导或独立完成的描述可得7分；团队负责不等于个人负责。", evidence["owned"], "明确个人负责的模块或决策范围。")
    add("results.outcome", "成果表达", "交付与结果", 10, min(10, len(evidence["outcomes"]) * 4), "每条带指标或可验收的交付、反馈结果4分，最多10分；无关数量不计。", evidence["outcomes"], "补充真实结果、交付或验收反馈。")
    add("results.conditions", "成果表达", "验证条件", 5, min(5, len(evidence["conditions"]) * 3), "每条明确的测试条件、样本量、基线或验收记录3分，最多5分。", evidence["conditions"], "补充测试环境、对照条件或验收记录。")
    add("results.comparison", "成果表达", "前后对比", 5, min(5, len(evidence["comparisons"]) * 3), "每条带指标的前后对比3分，最多5分；仅数字本身不加分。", evidence["comparisons"], "有依据时补充优化前后对比。")
    compact_length = len(re.sub(r"\s+", "", normal))
    length_points = 6 if 200 <= compact_length <= 8000 else (3 if 100 <= compact_length <= 12000 else 1)
    add("clarity.length", "表达清晰度", "篇幅适当", 6, length_points, "去除排版空白后200—8000字符得6分，100—12000字符得3分，其他得1分。", units[:1], "适度精简或补充正文。")
    items[-1]["observation"] = f"本次去空白正文为 {compact_length} 字符。"
    content_units = [unit for unit in units if len(unit["text"]) >= 12]
    distinct = len({unit["id"] for unit in content_units})
    repeat_points = 5 if content_units and distinct / len(content_units) >= .8 else (2 if content_units else 0)
    add("clarity.repetition", "表达清晰度", "避免重复", 5, repeat_points, "有效描述去重比例至少80%得5分，否则2分；没有有效描述得0分。", content_units[:2], "合并重复描述，保留独立事实。")
    items[-1]["observation"] = f"本次有效描述 {len(content_units)} 条，去重后 {distinct} 条。"
    useful = min(4, len(experience) + bool(education) + bool(skill_units))
    add("clarity.specificity", "表达清晰度", "信息具体", 4, useful, "教育、技能正文各1分，具体行动每条1分，最多4分。", education[:1] + skill_units[:1] + experience[:2])
    dimensions = []
    for label, maximum in DIMENSIONS.items():
        group = [item for item in items if item["dimension"] == label]
        earned = sum(item["points"] for item in group)
        dimensions.append({"label": label, "score": rounded(earned * 100 / maximum), "points": earned,
                           "max_points": maximum, "detail": f"实得 {earned}/{maximum} 分，展开逐项依据可查看原句与规则。"})
    requirements = requirement_items(text, job, evidence)
    job_score = rounded(sum(item["points"] for item in requirements)) if requirements else None
    matched = list(dict.fromkeys(name for item in requirements for name in item["matched"]))
    missing = list(dict.fromkeys(name for item in requirements if not item["matched"] for name in (item["skills"] or [item["title"]])))
    overall = sum(item["points"] for item in items)
    suggestions = [{"priority": "高" if item["max_points"] - item["points"] >= 5 else "中", "title": item["title"], "detail": item["missing"]}
                   for item in sorted(items, key=lambda item: item["points"] - item["max_points"]) if item["missing"]][:6]
    if missing:
        suggestions.insert(0, {"priority": "高", "title": "核对岗位要求", "detail": "未提供证据的要求：" + "、".join(missing) + "。仅补充真实经验。"})
    strengths = [item["title"] + "已有原文依据。" for item in items if item["points"] == item["max_points"] and item["max_points"] >= 5][:5]
    original = next((unit["quote"] for unit in experience if len(unit["quote"]) <= 300), "")
    return {"source": "local", "overall_score": overall, "job_match_score": job_score,
            "summary": f"按固定条目计算，简历质量 {overall}/100。" + (f"岗位要求匹配 {job_score}/100。" if job_score is not None else "提供具体岗位描述可评估要求匹配。"),
            "profile": {"name": name if has_name else "未识别", "email": email.group() if email else "未识别", "phone": phone.group() if phone else "未识别", "education": degree},
            "dimensions": dimensions, "score_items": items, "requirements": requirements,
            "skills": skills, "matched_skills": matched, "missing_skills": missing, "strengths": strengths,
            "suggestions": suggestions or [{"priority": "中", "title": "核实经历依据", "detail": "核对成果、个人职责与验证条件，准备面试材料。"}],
            "rewrites": [{"before": original, "after": original.rstrip("。.") + "；个人负责[具体范围]，通过[方法]，在[验证条件]下取得[真实结果]。"}] if original else [],
            "interview_questions": ["选一段经历说明个人职责、方案选择与结果之间的关系。", "成果的对照基准、验证条件和个人贡献分别是什么？"],
            "context": context, "meta": {"characters": len(text), "analyzed_at": datetime.now(timezone.utc).isoformat(),
                                         "model": None, "job_provided": bool(job), "scoring_version": VERSION, "scoring_source": "rules"}}
