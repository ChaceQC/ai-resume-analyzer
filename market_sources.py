"""国内企业官网公开招聘接口；仅由更新命令调用。"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from market import level_for_title, role_for_title


CHINA_TIME = timezone(timedelta(hours=8))
TENCENT = "https://careers.tencent.com/tencentcareer/api/post/"
NETEASE = "https://hr.163.com/api/hr163/position/queryPage"
MEITUAN = "https://zhaopin.meituan.com/api/official/job/getJobList"


def collect_domestic(proxy=None, cache_dir=None):
    entries, sources = [], []
    cache_dir = Path(cache_dir) if cache_dir else None
    with httpx.Client(proxy=proxy, timeout=25, headers={"User-Agent": "ResumeLens/3.0 (public recruitment snapshot)"}) as client:
        def collect(name, url, collector):
            captured = []

            def request(key, endpoint, *, params=None, body=None):
                cached = cache_dir / f"resume-market-cn-{key}.json" if cache_dir else None
                now = datetime.now(timezone.utc)
                if cached and cached.exists() and now - datetime.fromtimestamp(cached.stat().st_mtime, timezone.utc) < timedelta(hours=24):
                    payload = json.loads(cached.read_text(encoding="utf-8"))
                    fetched = datetime.fromtimestamp(cached.stat().st_mtime, timezone.utc)
                else:
                    response = client.get(endpoint, params=params) if body is None else client.post(endpoint, json=body)
                    response.raise_for_status()
                    payload, fetched = response.json(), now
                    if cached:
                        cached.parent.mkdir(parents=True, exist_ok=True)
                        cached.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                captured.append(fetched.isoformat())
                return payload

            try:
                jobs, raw_count = collector(request)
                if not jobs:
                    raise ValueError("empty source")
                entries.extend(jobs)
                sources.append({"company": name, "url": url, "captured_at": min(captured), "raw_count": raw_count})
                print(f"{name}: 获取 {len(jobs)} 条职位（原始 {raw_count} 条）", flush=True)
            except (httpx.HTTPError, OSError, KeyError, TypeError, ValueError) as exc:
                print(f"{name}: 更新失败 ({type(exc).__name__})", flush=True)

        collect("腾讯", TENCENT + "Query", fetch_tencent)
        collect("网易", NETEASE, fetch_netease)
        collect("美团", MEITUAN, fetch_meituan)
    return entries, sources


def fetch_tencent(request):
    def page(index):
        payload = request(f"tencent-list-{index}", TENCENT + "Query",
                          params={"pageIndex": index, "pageSize": 100, "language": "zh-cn", "area": "cn"})
        if payload["Code"] != 200:
            raise ValueError("Tencent API error")
        return payload["Data"]

    first = page(1)
    posts = list(first["Posts"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        for data in pool.map(page, range(2, (first["Count"] + 99) // 100 + 1)):
            posts.extend(data["Posts"])
        recent = []
        for item in posts:
            updated = datetime.strptime(item["LastUpdateTime"], "%Y年%m月%d日").replace(tzinfo=CHINA_TIME)
            if role_for_title(item["RecruitPostName"]) and updated >= datetime.now(timezone.utc) - timedelta(days=60):
                recent.append(item)

        def detail(item):
            payload = request(f"tencent-{item['PostId']}", TENCENT + "ByPostId",
                              params={"postId": item["PostId"], "language": "zh-cn"})
            if payload["Code"] != 200 or not payload.get("Data"):
                raise ValueError("Tencent detail unavailable")
            data = payload["Data"]
            return {"id": data["PostId"], "company": "腾讯", "industry": "software", "title": data["RecruitPostName"],
                    "location": ", ".join(filter(None, [data["LocationName"], item.get("CountryName")])),
                    "url": f"https://careers.tencent.com/jobdesc.html?postId={data['PostId']}",
                    "updated_at": datetime.strptime(data["LastUpdateTime"], "%Y年%m月%d日").replace(tzinfo=CHINA_TIME).isoformat(),
                    "content": "任职要求\n" + data["Requirement"]}

        jobs = list(pool.map(detail, recent))
    return jobs, first["Count"]


def fetch_netease(request):
    def page(index):
        payload = request(f"netease-{index}", NETEASE, body={"currentPage": index, "pageSize": 100})
        if payload["code"] != 200:
            raise ValueError("NetEase API error")
        return payload["data"]

    first = page(1)
    posts = list(first["list"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        for data in pool.map(page, range(2, first["pages"] + 1)):
            posts.extend(data["list"])
    jobs = [{"id": str(item["id"]), "company": "网易", "industry": "software", "title": item["name"],
             "location": "、".join(item["workPlaceNameList"]),
             "url": f"https://hr.163.com/job-detail.html?id={item['id']}&lang=zh",
             "updated_at": datetime.fromtimestamp(item["updateTime"] / 1000, timezone.utc).isoformat(),
             "content": "任职要求\n" + item["requirement"],
             "level": "graduate" if str(item.get("workType")) == "1" else level_for_title(item["name"])} for item in posts]
    return jobs, first["total"]


def fetch_meituan(request):
    def page(index):
        payload = request(f"meituan-{index}", MEITUAN,
                          body={"page": {"pageNo": index, "pageSize": 100}, "jobShareType": "1", "keywords": "",
                                "cityList": [], "department": [], "jfJgList": [], "jobType": [], "typeCode": [], "specialCode": []})
        if payload["status"] != 1 or not payload.get("data"):
            raise ValueError("Meituan API error")
        return payload["data"]

    first = page(1)
    posts = list(first["list"])
    total = first["page"]["totalCount"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for data in pool.map(page, range(2, (total + 99) // 100 + 1)):
            posts.extend(data["list"])
    jobs = [{"id": item["jobUnionId"], "company": "美团", "industry": "software", "title": item["name"],
             "location": "、".join(city["name"] for city in item["cityList"]),
             "url": f"https://zhaopin.meituan.com/web/position/detail?jobUnionId={item['jobUnionId']}",
             "updated_at": datetime.fromtimestamp(item["refreshTime"] / 1000, timezone.utc).isoformat(),
             "content": "任职要求\n" + (item.get("jobRequirement") or ""),
             "level": "graduate" if str(item.get("jobType")) == "1" else level_for_title(item["name"])}
            for item in posts if item.get("refreshTime") and item.get("cityList")]
    return jobs, total
