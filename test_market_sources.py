"""国内招聘接口的分页、日期和字段映射回归；数据为测试构造，不联网。"""

import unittest
from datetime import datetime

from market_sources import CHINA_TIME, fetch_meituan, fetch_netease, fetch_tencent


class DomesticSourceTests(unittest.TestCase):
    def test_meituan_pagination_and_incomplete_locations(self):
        calls = []
        post = {"jobUnionId": "test-post", "name": "软件开发工程师", "cityList": [{"name": "北京市"}],
                "refreshTime": 1793850000000, "jobRequirement": "熟悉 Python", "jobType": "1"}

        def request(key, url, **options):
            calls.append(options["body"]["page"]["pageNo"])
            index = options["body"]["page"]["pageNo"]
            return {"status": 1, "data": {"list": [post, dict(post, cityList=None)] if index == 1 else [dict(post, jobUnionId="test-second")],
                                          "page": {"totalCount": 101}}}

        jobs, raw_count = fetch_meituan(request)
        self.assertEqual(raw_count, 101)
        self.assertEqual(sorted(calls), [1, 2])
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["level"], "graduate")
        self.assertEqual(jobs[0]["location"], "北京市")
        self.assertEqual(datetime.fromisoformat(jobs[0]["updated_at"]).timestamp(), 1793850000)

    def test_netease_pagination_keeps_requirements_and_internship_flag(self):
        calls = []

        def request(key, url, **options):
            index = options["body"]["currentPage"]
            calls.append(index)
            return {"code": 200, "data": {"pages": 2, "total": 101, "list": [
                {"id": index, "name": "游戏客户端开发", "workPlaceNameList": ["杭州市", "广州市"],
                 "updateTime": 1793850000000, "requirement": "熟悉 Python、C++", "workType": "1"}]}}

        jobs, raw_count = fetch_netease(request)
        self.assertEqual((len(jobs), raw_count), (2, 101))
        self.assertEqual(sorted(calls), [1, 2])
        self.assertEqual(jobs[0]["level"], "graduate")
        self.assertIn("杭州市", jobs[0]["location"])
        self.assertEqual(jobs[0]["content"], "任职要求\n熟悉 Python、C++")
        self.assertIn("job-detail.html?id=1&lang=zh", jobs[0]["url"])

    def test_tencent_reads_detail_requirements_and_local_update_date(self):
        date = datetime.now(CHINA_TIME).strftime("%Y年%m月%d日")
        post = {"PostId": "test-post", "RecruitPostName": "软件开发工程师", "LocationName": "深圳", "CountryName": "中国", "LastUpdateTime": date}
        calls = []

        def request(key, url, **options):
            calls.append(url)
            return {"Code": 200, "Data": {"Count": 1, "Posts": [post]} if url.endswith("Query") else dict(post, Requirement="熟悉 Python、SQL")}

        jobs, raw_count = fetch_tencent(request)
        self.assertEqual((len(jobs), raw_count), (1, 1))
        self.assertTrue(calls[-1].endswith("ByPostId"))
        self.assertEqual(jobs[0]["content"], "任职要求\n熟悉 Python、SQL")
        self.assertEqual(datetime.fromisoformat(jobs[0]["updated_at"]).utcoffset().total_seconds(), 8 * 3600)
        self.assertTrue(jobs[0]["url"].startswith("https://careers.tencent.com/"))


if __name__ == "__main__":
    unittest.main()
