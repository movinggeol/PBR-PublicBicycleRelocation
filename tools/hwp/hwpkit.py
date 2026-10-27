"""한글(HWP) 자동화 얇은 도구. comtypes 동적 디스패치로 HWPFrame.HwpObject를 부른다.

한컴오피스 한글(2020에서 확인)이 깔린 윈도우에서만 돈다. 파일을 열거나 그림을 넣을 때 한글이 보안 승인 창('글')을
띄우므로 사람이 '허용'을 눌러 주어야 한다(프로세스마다 대략 한 번).
"""
import os

try:
    import comtypes.client as cc
except ImportError as e:  # 선택 의존성이다(requirements.txt의 한글 변환 절)
    raise SystemExit("comtypes가 없습니다: .venv\\Scripts\\python.exe -m pip install comtypes") from e

BODY_FONT = "한양신명조"


class Hwp:
    def __init__(self, visible=True):
        self.h = cc.CreateObject("HWPFrame.HwpObject", dynamic=True)
        if visible:
            self.h.XHwpWindows.Item(0).Visible = True
        self.act = self.h.HAction
        self.ps = self.h.HParameterSet
        self._boxed = False

    # 단위
    def mm(self, v):
        return self.h.MiliToHwpUnit(float(v))

    def pt(self, v):
        return self.h.PointToHwpUnit(float(v))

    def run(self, name):
        return self.act.Run(name)

    # 쪽
    def page_setup(self, w=182, hgt=257, left=25, right=25, top=15, bottom=15, header=10, footer=10):
        p = self.ps.HSecDef
        self.act.GetDefault("PageSetup", p.HSet)
        d = p.PageDef
        d.PaperWidth, d.PaperHeight = self.mm(w), self.mm(hgt)
        d.LeftMargin, d.RightMargin = self.mm(left), self.mm(right)
        d.TopMargin, d.BottomMargin = self.mm(top), self.mm(bottom)
        d.HeaderLen, d.FooterLen, d.GutterLen = self.mm(header), self.mm(footer), 0
        d.Landscape = 0
        p.HSet.SetItem("ApplyClass", 24)
        p.HSet.SetItem("ApplyTo", 3)
        self.act.Execute("PageSetup", p.HSet)

    # 글자 · 문단
    def char(self, size=10.0, bold=False, font=BODY_FONT, ratio=100, spacing=0, color=0):
        p = self.ps.HCharShape
        self.act.GetDefault("CharShape", p.HSet)
        p.Height = self.pt(size)
        p.Bold = 1 if bold else 0
        p.Italic = 0
        p.TextColor = color  # 0xBBGGRR
        for lang in ("Hangul", "Latin", "Hanja", "Japanese", "Other", "Symbol"):
            setattr(p, "FaceName" + lang, font)
            setattr(p, "FontType" + lang, 2 if font.startswith("한양") else 1)
            setattr(p, "Ratio" + lang, ratio)
            setattr(p, "Spacing" + lang, spacing)
        if not self.act.Execute("CharShape", p.HSet):
            raise RuntimeError("글자 모양 실패")

    def para(self, align="justify", line=200, indent=0.0, left=0.0, before=0.0, after=0.0, tabs=None, keep=False,
             box=False):
        p = self.ps.HParaShape
        self.act.GetDefault("ParagraphShape", p.HSet)
        # box: 옅은 회색 바탕을 깔고 이웃 문단과 이어 한 상자로 만든다(코드). 새 문단은 앞 문단 모양을
        # 물려받으므로, 상자를 쓴 뒤에는 바탕 · 연결 · 여백을 모두 되돌려야 뒤 문단까지 번지지 않는다
        if box or self._boxed:
            fa = p.BorderFill.FillAttr
            fa.type = 1 if box else 0
            fa.WinBrushFaceColor = fa.WinBrushHatchColor = 0xF2F2F2 if box else 0xFFFFFFFF
            p.BorderConnect = 1 if box else 0
            p.BorderOffsetLeft = p.BorderOffsetRight = self.mm(2) if box else 0
            p.BorderOffsetTop = p.BorderOffsetBottom = self.mm(1) if box else 0
            self._boxed = box
        p.KeepWithNext = 1 if keep else 0
        p.AlignType = {"justify": 0, "left": 1, "right": 2, "center": 3}[align]
        p.LineSpacingType = 0
        p.LineSpacing = line
        p.Indentation = self.pt(indent)
        p.LeftMargin = self.pt(left)
        p.PrevSpacing = self.pt(before)
        p.NextSpacing = self.pt(after)
        if tabs is not None:
            td = p.TabDef
            td.CreateItemArray("TabItem", 3 * len(tabs)); arr = td.TabItem
            # 한 항목은 (위치, 채움, 종류) 순이다. 채움 3은 점선. 문단에 이미 탭이 있으면 새 정의가 무시되기도 한다
            for i, (pos, kind, *fill) in enumerate(tabs):
                arr.Item[3 * i] = self.mm(pos)
                arr.Item[3 * i + 1] = fill[0] if fill else 0
                arr.Item[3 * i + 2] = {"left": 0, "right": 1, "center": 2}[kind]
        if not self.act.Execute("ParagraphShape", p.HSet):
            raise RuntimeError("문단 모양 실패")

    # 넣기
    def text(self, s):
        if not s:
            return
        p = self.ps.HInsertText
        self.act.GetDefault("InsertText", p.HSet)
        p.Text = s
        self.act.Execute("InsertText", p.HSet)

    def enter(self):
        self.run("BreakPara")

    def page_break(self):
        self.run("BreakPage")

    def equation(self, script, size=10.0):
        p = self.ps.HEqEdit
        self.act.GetDefault("EquationCreate", p.HSet)
        p.EqFontName = "HancomEQN"
        p.string = script
        p.BaseUnit = self.pt(size)
        self.act.Execute("EquationCreate", p.HSet)

    def table(self, rows, cols, col_widths_mm, split=False):
        """split: 글자처럼 취급하지 않고 쪽 끝에서 셀 단위로 나눈다(긴 표). 아니면 글자처럼(수식 칸)."""
        p = self.ps.HTableCreation
        self.act.GetDefault("TableCreate", p.HSet)
        p.Rows, p.Cols = rows, cols
        p.WidthType = 2
        p.HeightType = 0
        p.WidthValue = self.mm(sum(col_widths_mm))
        p.CreateItemArray("ColWidth", cols); arr = p.ColWidth
        for i, w in enumerate(col_widths_mm):
            arr.Item[i] = self.mm(w - 3.6)  # 칸 안 여백(좌우 1.8mm)은 열 너비 밖에 붙는다
        p.TableProperties.Width = self.mm(sum(col_widths_mm))
        self.act.Execute("TableCreate", p.HSet)
        # 배치는 만든 뒤에 표 속성으로 준다. TableCreate의 TableProperties는 한 프로세스에서 처음 만든 표의
        # 배치만 먹고 그 뒤로는 무시된다(첫 표가 글자처럼이면 모든 표가 글자처럼이 되어 긴 표가 꼬리말까지 넘쳤다)
        t = self.ps.HShapeObject
        self.act.GetDefault("TablePropertyDialog", t.HSet)
        if split:
            # 자리 차지 · 문단 기준 가운데 · 셀 단위로 나눔(2) · 제목 줄 반복
            t.TreatAsChar, t.TextWrap, t.FlowWithText = 0, 1, 1
            t.VertRelTo, t.HorzRelTo, t.HorzAlign = 3, 3, 1
            t.PageBreak, t.RepeatHeader = 2, 1
        else:
            t.TreatAsChar = 1
        if not self.act.Execute("TablePropertyDialog", t.HSet):
            raise RuntimeError("표 속성 실패")

    def page_num(self, roman=False):
        """이 구역의 쪽 번호: 아래 가운데, '- 1 -' 꼴(앞붙이는 로마 소문자)."""
        p = self.ps.HPageNumPos
        self.act.GetDefault("PageNumPos", p.HSet)
        p.DrawPos, p.NumberFormat, p.SideChar = 5, 3 if roman else 0, ord("-")
        return self.act.Execute("PageNumPos", p.HSet)

    def new_number(self, n=1):
        p = self.ps.HAutoNum
        self.act.GetDefault("NewNumber", p.HSet)
        p.NumType, p.NewNumber = 0, n
        return self.act.Execute("NewNumber", p.HSet)

    def replace_all(self, find, repl):
        p = self.ps.HFindReplace
        self.act.GetDefault("AllReplace", p.HSet)
        p.FindString, p.ReplaceString = find, repl
        p.IgnoreMessage = 1
        p.Direction = 2  # 문서 전체
        return self.act.Execute("AllReplace", p.HSet)

    def find_next(self, s):
        """s를 앞으로 찾아 선택한다. 찾으면 True."""
        p = self.ps.HFindReplace
        self.act.GetDefault("RepeatFind", p.HSet)
        p.FindString = s
        p.IgnoreMessage = 1
        p.Direction = 0
        return self.act.Execute("RepeatFind", p.HSet)

    def set_height(self, size):
        """선택한 글자의 크기만 바꾼다."""
        p = self.ps.HCharShape
        self.act.GetDefault("CharShape", p.HSet)
        p.Height = self.pt(size)
        return self.act.Execute("CharShape", p.HSet)

    def hide_page_number(self):
        """커서가 있는 쪽의 쪽 번호(와 머리말 · 꼬리말)를 감춘다."""
        p = self.ps.HPageHiding
        self.act.GetDefault("PageHiding", p.HSet)
        p.Fields = 0x23  # 머리말 0x01 · 꼬리말 0x02 · 쪽 번호 0x20
        return self.act.Execute("PageHiding", p.HSet)

    def insert_file(self, path):
        p = self.ps.HInsertFile
        self.act.GetDefault("InsertFile", p.HSet)
        p.FileName = os.path.abspath(path)
        p.KeepSection, p.KeepCharshape, p.KeepParashape, p.KeepStyle = 1, 1, 1, 1
        return self.act.Execute("InsertFile", p.HSet)

    def header_row(self):
        """커서가 든 표 첫 칸의 행을 제목 셀로 지정한다(쪽이 바뀌면 그 행이 다시 찍힌다)."""
        self.run("TableCellBlock")
        self.run("TableCellBlockRow")
        p = self.ps.HShapeObject
        self.act.GetDefault("TablePropertyDialog", p.HSet)
        p.HSet.SetItem("ShapeType", 3)
        p.HSet.SetItem("ShapeCellSize", 0)
        p.ShapeTableCell.Header = 1
        r = self.act.Execute("TablePropertyDialog", p.HSet)
        self.run("Cancel")
        self.run("TableColBegin")  # 행을 고르고 나면 커서가 행 끝 칸에 남는다. 첫 칸으로 돌린다
        return r

    def no_borders(self):
        """커서가 든 표의 모든 칸 테두리를 없앤다."""
        for a in ("TableCellBlock", "TableCellBlockExtend", "TableCellBlockExtend"):
            self.run(a)
        p = self.ps.HCellBorderFill
        self.act.GetDefault("CellBorderFill", p.HSet)
        for k in ("BorderTypeLeft", "BorderTypeRight", "BorderTypeTop", "BorderTypeBottom", "TypeHorz", "TypeVert"):
            setattr(p, k, 0)
        self.act.Execute("CellBorderFill", p.HSet)
        self.run("Cancel")

    def picture(self, path, width_mm, height_mm):
        return self.h.InsertPicture(os.path.abspath(path), True, 1, False, False, 0,
                                    int(width_mm), int(height_mm))

    def save(self, path, fmt="HWP"):
        return self.h.SaveAs(os.path.abspath(path), fmt, "")

    def quit(self):
        self.h.Clear(1)
        self.h.Quit()
