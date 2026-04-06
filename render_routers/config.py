from typing import Set
import os

LOSTARK_API_PROFILE_KEY = os.getenv("LOSTARK_API_PROFILE_KEY")
LOSTARK_API_SIBLINGS_KEY = os.getenv("LOSTARK_API_SIBLINGS_KEY")
SPECIAL_NICKNAME: Set[str] = {"조교병", "제주화강암망치", "건슬링어누나너무이뻐요", "탁서윤", "축복을그대에게"}
MOKOKO_NICKNAME: Set[str] = {"회복강화"}