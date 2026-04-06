from datetime import datetime
from typing import Optional, Dict, Any

def format_datetime_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """datetime 객체를 문자열로 변환"""
    if not data:
        return data
    
    for field in ['start_date', 'joined_at', 'created_at', 'updated_at', 'expires_at']:
        if data.get(field) and hasattr(data[field], 'strftime'):
            data[field] = data[field].strftime('%Y-%m-%d %H:%M:%S')
    
    return data

def parse_start_date(start_date_str: str) -> Optional[str]:
    """시작일 문자열을 DB 형식으로 파싱"""
    if not start_date_str:
        return None
    
    try:
        date_part = start_date_str.split("(")[0].strip()
        time_part = start_date_str.split(" ")[-1] if " " in start_date_str else "00:00"
        
        if len(date_part.split(".")) == 3:
            year_part = date_part.split(".")[0]
            year = 2000 + int(year_part) if len(year_part) == 2 else int(year_part)
            month = int(date_part.split(".")[1])
            day = int(date_part.split(".")[2])
            
            hour, minute = 0, 0
            if ":" in time_part:
                hour, minute = map(int, time_part.split(":"))
            
            return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00"
    except Exception:
        pass
    
    return start_date_str