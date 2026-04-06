from .http_client import http_client

raid_list = []
raid_difficulty_map = {}

async def load_raids():
    global raid_list, raid_difficulty_map
    try:
        response = await http_client.get("/raid/")
        if response.status_code == 200:
            data = response.json()
            raids = data.get('data', [])
            seen = set()
            raid_list = []
            for raid in raids:
                name = raid["name"]
                if name not in seen:
                    raid_list.append(name)
                    seen.add(name)
            # 난이도 맵 생성
            raid_difficulty_map = {}
            for raid in raids:
                name = raid["name"]
                diff = raid["difficulty"]
                if name not in raid_difficulty_map:
                    raid_difficulty_map[name] = []
                if diff and diff not in raid_difficulty_map[name]:
                    raid_difficulty_map[name].append(diff)
        print(f"Load raid_data : {raid_list}")
    except Exception as e:
        print(f"[Raids Error] {e}")