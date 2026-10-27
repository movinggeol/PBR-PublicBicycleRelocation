"""본문의 저자-연도 인용을 처음 나온 순서의 번호로 바꾼다(한글_옮기기.md 4장).

목록 모양은 4-2의 꼴이다: 저자, "논문명", 학술지, 권(호), pp. 쪽–쪽, 연도. 원본은 참고문헌.md이다.
"""
import re

# (첫 저자, 연도) → 목록 항목
ENTRIES = {
    ("김영일", 2022): "김영일, “[동행취재] 타슈2 수거 담당자의 바람 \"파손만 안 됐으면 좋겠어요\"”, 중도일보, 2022.12.13. https://m.joongdo.co.kr/view.php?key=20221213010003215",
    ("김유신", 2023): "김유신, “공유자전거 따릉이 재배치를 위한 실시간 수요예측 모델 연구”, 인터넷정보학회논문지, 24(5), pp. 107–120, 2023.",
    ("대전광역시", 2024): "대전광역시, “2025 대전광역시 도시교통정비 중기계획”, 대전광역시, 2024.",
    ("대전광역시", 2025): "대전광역시, “2025년도 대전광역시 교통현황조사 및 분석보고서”, 대전광역시, 2025.",
    ("문현수", 2016): "문현수, 이영석, “대전시 공공 자전거(타슈) 공개 데이터 시각화 및 분석”, 정보과학회 컴퓨팅의 실제 논문지, 22(6), pp. 253–267, 2016.",
    ("민지원", 2017): "민지원, 문현수, 이영석, “랜덤 포레스트를 이용한 대전시 공공 자전거('타슈') 수요 예측”, 2017년 한국컴퓨터종합학술대회 논문집, pp. 969–971, 2017.",
    ("박정연", 2024): "박정연, 박지환, 이상인, “대전광역시 공공자전거 이용량 수요 예측모형 연구”, 한국데이터정보과학회지, 35(5), pp. 667–680, 2024.",
    ("소윤상", 2018): "소윤상, “LSTM을 이용한 공공자전거 동적 상태의 재고 수준 연구”, 서울대학교 환경대학원 석사학위논문, 2018.",
    ("윤은옥", 2024): "윤은옥, 김강민, 박혜성, 정성욱, “효율적인 공공 자전거 재배치를 위한 실시간 자전거 수요량 기반의 HDPRA 기법 제안”, 한국정보전자통신기술학회논문지, 17(2), pp. 83–92, 2024.",
    ("이상복", 2018): "이상복, 임희종, 정광헌, “서울시 공공자전거 시스템 운영을 위한 효율적 관리 구역 설정”, 경영과학, 35(1), pp. 55–67, 2018.",
    ("이은탁", 2019): "이은탁, 손봉수, “이용수요 기반의 서울시 공공자전거 재배치전략 도출”, 대한교통학회지, 37(1), pp. 27–38, 2019.",
    ("조근민", 2020): "조근민, 이상수, 남두희, “딥러닝 모형을 활용한 공공자전거 대여량 예측에 관한 연구”, 한국ITS학회논문지, 19(3), pp. 28–37, 2020.",
    ("지창훈", 2007): "지창훈, 김미이, 이영훈, “배달과 수집을 수행하는 차량경로문제 휴리스틱에 관한 연구: 수도권 레미콘 운송사례”, 경영과학, 24(2), pp. 143–156, 2007.",
    ("한지혜", 2024): "한지혜, “대전교통공사, AI 활용 타슈 배치 효율성 높인다”, 디트뉴스24, 2024.7.24. https://www.dtnews24.com/news/articleView.html?idxno=775777",
    ("Contardo", 2012): "C. Contardo, C. Morency, and L.-M. Rousseau, “Balancing a dynamic public bike-sharing system”, Technical Report CIRRELT-2012-09, CIRRELT, 2012.",
    ("Ghosh", 2017): "S. Ghosh, P. Varakantham, Y. Adulyasak, and P. Jaillet, “Dynamic repositioning to reduce lost demand in bike sharing systems”, Journal of Artificial Intelligence Research, 58, pp. 387–430, 2017.",
    ("O'Mahony", 2015): "E. O'Mahony and D. B. Shmoys, “Data analysis and optimization for (Citi)Bike sharing”, Proceedings of the Twenty-Ninth AAAI Conference on Artificial Intelligence, pp. 687–694, 2015.",
    ("Raviv", 2013): "T. Raviv, M. Tzur, and I. A. Forma, “Static repositioning in a bike-sharing system: Models and solution approaches”, EURO Journal on Transportation and Logistics, 2(3), pp. 187–229, 2013.",
    ("Schuijbroek", 2017): "J. Schuijbroek, R. C. Hampshire, and W.-J. van Hoeve, “Inventory rebalancing and vehicle routing in bike sharing systems”, European Journal of Operational Research, 257(3), pp. 992–1004, 2017.",
    # 자료 출처: <표 4-1>의 출처 칸에 번호를 단다(4-2)
    ("@data", 0): "공공데이터포털, “대전시 공영자전거 타슈 대여이력 정보”, https://www.data.go.kr/",
    ("@kma", 0): "기상청, “기상자료개방포털 종관기상관측(ASOS) 시간 자료”, https://data.kma.go.kr/",
    ("@tashu", 0): "대전광역시, “공영자전거 타슈”, https://bike.tashu.or.kr/",
    ("@tmap", 0): "티맵모빌리티, “TMAP API”, https://tmapapi.tmapmobility.com/",
}
SOURCES = [("공공데이터포털", "@data"), ("기상청", "@kma"), ("타슈 공개 API", "@tashu"), ("TMAP", "@tmap")]

NAME = r"[가-힣A-Za-z']+"
NARRATIVE = re.compile(r"(?P<name>%s)(?P<rest> 외|·[가-힣]+|와 [A-Za-z']+)?\((?P<y>\d{4})\)" % NAME)
PAREN = re.compile(r"\(([^()]*\d{4})\)")
ITEM = re.compile(r"^(?P<name>%s)(?: 외|·[가-힣]+)?, (?P<y>\d{4})$" % NAME)


class Refs:
    def __init__(self):
        self.order = []
        self.unknown = []

    def num(self, key):
        if key not in self.order:
            self.order.append(key)
        return self.order.index(key) + 1

    def cite(self, text):
        def paren(m):
            items = [ITEM.match(s.strip()) for s in m.group(1).split(";")]
            if not all(items) or not all((i["name"], int(i["y"])) in ENTRIES for i in items):
                if all(items):
                    self.unknown.append(m.group(0))
                return m.group(0)
            return "".join("[%d]" % self.num((i["name"], int(i["y"]))) for i in items)

        def narr(m):
            key = (m["name"], int(m["y"]))
            if key not in ENTRIES:
                self.unknown.append(m.group(0))
                return m.group(0)
            return "%s%s[%d]" % (m["name"], m["rest"] or "", self.num(key))

        text = PAREN.sub(paren, text)
        return NARRATIVE.sub(narr, text)

    def source(self, cell):
        """<표 4-1> 출처 칸: 자료 출처 이름 뒤에 번호를 단다."""
        for word, key in SOURCES:
            if word in cell:
                return cell + "[%d]" % self.num((key, 0))
        return cell

    def listing(self):
        return ["[%d] %s" % (i + 1, ENTRIES[k]) for i, k in enumerate(self.order)]

    def uncited(self):
        return [ENTRIES[k][:30] for k in ENTRIES if k not in self.order]
