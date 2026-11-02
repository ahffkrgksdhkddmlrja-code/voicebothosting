import json

shop = {
    "[테스트] 아메시스트 등급업 눈알 (750개)": {
        "price": 750,
        "description": "(테스트용) 토파즈 -> 아메시스트 등급업에 필요한 눈알 750개"
    },
    "[테스트] 앰버 등급업 눈알 (1600개)": {
        "price": 1600,
        "description": "(테스트용) 아메시스트 -> 앰버 등급업에 필요한 눈알 1,600개"
    },
    "[테스트] 에메랄드 등급업 눈알 (2500개)": {
        "price": 2500,
        "description": "(테스트용) 앰버 -> 에메랄드 등급업에 필요한 눈알 2,500개"
    },
    "[테스트] 골드 등급업 눈알 (3100개)": {
        "price": 3100,
        "description": "(테스트용) 에메랄드 -> 골드 등급업에 필요한 눈알 3,100개"
    },
    "[테스트] 루비 등급업 눈알 (4200개)": {
        "price": 4200,
        "description": "(테스트용) 골드 -> 루비 등급업에 필요한 눈알 4,200개"
    },
    "[테스트] 사파이어 등급업 눈알 (6500개)": {
        "price": 6500,
        "description": "(테스트용) 루비 -> 사파이어 등급업에 필요한 눈알 6,500개"
    },
    "[테스트] 오닉스 등급업 눈알 (8000개)": {
        "price": 8000,
        "description": "(테스트용) 사파이어 -> 오닉스 등급업에 필요한 눈알 8,000개"
    },
    "[테스트] 다이아몬드 등급업 눈알 (10000개)": {
        "price": 10000,
        "description": "(테스트용) 오닉스 -> 다이아몬드 등급업에 필요한 눈알 10,000개"
    },
}

with open(r"C:\Users\ahffk\Desktop\시지프 백화점 목소리 봇\shop.json", "w", encoding="utf-8") as f:
    json.dump(shop, f, ensure_ascii=False, indent=4)

print("완료! 항목 수:", len(shop))
for name, info in shop.items():
    print(f"  {name}: {info['price']}개")
