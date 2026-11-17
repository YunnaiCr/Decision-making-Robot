"""外部数据源的实跑检查：真的打高德接口。

    AMAP_KEY=xxx python scripts/check_sources.py 北京 电影院

没配 key 会直接说不可用，不会假装成功。这个脚本是「真实查询」的入口——
单测用抓下来的报文，这里用真接口，两边分开。
"""

import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_agent import sources


def main() -> None:
    city = sys.argv[1] if len(sys.argv) > 1 else "北京"
    keyword = sys.argv[2] if len(sys.argv) > 2 else "电影院"

    print("AMAP_KEY:", "已配置" if os.environ.get("AMAP_KEY") else "未配置")
    print()

    failed = 0

    print(f"== 实时天气 · {city} ==")
    try:
        print(sources.amap_weather(city))
    except sources.SourceError as exc:
        failed += 1
        print(f"[失败] {exc.kind}: {exc.message}")

    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    print()
    print(f"== 明天（{tomorrow}）的预报 ==")
    try:
        print(sources.amap_weather(city, tomorrow))
    except sources.SourceError as exc:
        failed += 1
        print(f"[失败] {exc.kind}: {exc.message}")

    print()
    print(f"== 地点搜索 · {city} · 「{keyword}」 ==")
    try:
        options = sources.amap_places(keyword, city)
        for option in options:
            hours = f"{option.open_min}-{option.close_min}" if option.open_min is not None else "时间未知"
            price = f"人均{option.price_per_person}" if option.price_per_person is not None else "价格未知"
            print(f"  {option.place_id} {option.name} [{option.category}] {price} {hours} tags={option.tags}")
    except sources.SourceError as exc:
        failed += 1
        print(f"[失败] {exc.kind}: {exc.message}")

    print()
    print("全部通过" if failed == 0 else f"{failed} 项失败")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
