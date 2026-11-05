"""评分材料准备、文本提取和原文定位；得分由AI依据细则判断。"""

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP


from rubric import VERSION, criteria
SECTIONS = {
    "education": r"教育背景|教育经历|学历|教育|education(?:al background)?|qualifications",
    "skills": r"专业技能|技术技能|技术栈|技能|核心能力|technical skills|skills|competencies",
    "experience": r"项目或工作经历|项目经历|相关实践|实践经历|研究经历|工作经历|工作经验|实习经历|项目经验|科研经历|项目|professional experience|work experience|employment history|work history|experience|projects?|internships?|research experience",
    "other": r"自我评价|个人评价|兴趣爱好|求职意向|个人信息|联系方式|summary|interests|references",
}
ACTION = r"负责|主导|独立|带领|编制|对账|签约|调研|构建|改进|改善|设计|开发|实现|优化|搭建|部署|交付|维护|排查|解决|重构|编写|分析|制定|策划|执行|推动|完成|提升|提高|降低|缩短|减少|组织|协调|审计|核算|采购|谈判|诊治|研究|(?<![a-z])(?:built|developed|designed|implemented|optimized|reduced|increased|delivered|led|managed|created|automated|deployed|resolved|improved|maintained|launched|analyzed|audited|negotiated|coordinated|researched)(?![a-z])"
METHOD = r"使用|通过|基于|采用|利用|借助|运用|引入|重构|缓存|索引|对照实验|(?<![a-z])(?:using|with|via|through|by|based on)(?![a-z])"
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


def resolve_context(job: str, context=None) -> dict:
    result = {"industry": "", "role": "", "level": "", "city": ""}
    result.update(context or {})
    if not result["role"]:
        title = normalize(job).splitlines()[0] if job.strip() else ""
        result["role"] = next((key for key, (_, pattern) in ROLES.items() if re.search(pattern, title, re.I)), "")
    if not result["industry"]:
        result["industry"] = next((key for key, pattern in {"finance": r"金融|银行|支付|fintech|banking|payments", "healthcare": r"医疗|医药|临床|healthcare|medical", "engineering": r"制造|供应链|汽车|manufacturing|automotive"}.items() if re.search(pattern, job, re.I)), "")
    return result


def prepare_report(text: str, job: str, context=None) -> dict:
    context = resolve_context(job, context)
    normal = normalize(text)
    email = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", normal)
    phone = re.search(r"(?<!\d)(?:\+86[ -]?)?1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)", normal)
    if not phone:
        phone = re.search(r"\+\d[\d ()-]{6,}\d", normal)
    first = next((line.strip() for line in normal.splitlines() if line.strip() and line.strip().casefold() not in {"简历", "个人简历", "resume", "curriculum vitae"}), "")
    name = re.sub(r"^(?:姓名|name)\s*[:：]?\s*", "", first, flags=re.I)
    has_name = bool(name) and len(name) <= 40 and not re.search(r"[@\d:：]|电话|邮箱|phone|email", name, re.I)
    degree = next((label for _, label, pattern in DEGREES if re.search(pattern, normal, re.I)), "未识别")
    return {"source": "pending", "overall_score": None, "job_match_score": None, "summary": "",
            "profile": {"name": name if has_name else "未识别", "email": email.group() if email else "未识别",
                        "phone": phone.group() if phone else "未识别", "education": degree},
            "score_items": criteria(), "dimensions": [], "requirements": [], "skills": candidate_skills(text),
            "matched_skills": [], "missing_skills": [], "strengths": [], "suggestions": [], "rewrites": [], "interview_questions": [],
            "context": context, "meta": {"characters": len(text), "analyzed_at": datetime.now(timezone.utc).isoformat(),
                                         "model": None, "job_provided": bool(job), "scoring_version": VERSION, "scoring_source": "ai"}}


def evidence_catalog(text: str, source="resume") -> list[dict]:
    """给解析后的原文片段编号，模型只需返回编号，无须重抄文字。"""
    records = []
    for unit in source_units(text, source):
        for offset in range(0, len(unit["quote"]), 700):
            raw = unit["quote"][offset:offset + 700]
            quote = raw.strip()
            if not quote:
                continue
            start = unit["start"] + offset + len(raw) - len(raw.lstrip())
            end = start + len(quote)
            records.append({"id": source + "-" + fingerprint([start, end, quote])[:12],
                            "source": source, "start": start, "end": end, "quote": quote})
    return records


def quote_reference(text: str, quote: str, source="resume") -> dict:
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 2000:
        raise ValueError("invalid evidence quote")
    quote = quote.strip()
    start = text.find(quote)
    if start >= 0:
        end = start + len(quote)
    else:
        characters, positions = [], []
        for index, char in enumerate(text):
            for converted in normalize(char):
                if not converted.isspace():
                    characters.append(converted)
                    positions.append(index)
        compact = re.sub(r"\s+", "", normalize(quote))
        offset = "".join(characters).find(compact)
        if not compact or offset < 0:
            raise ValueError("evidence quote not found in source")
        start, end = positions[offset], positions[offset + len(compact) - 1] + 1
    return {"id": fingerprint([source, start, end])[:16], "source": source, "start": start, "end": end, "quote": text[start:end]}
