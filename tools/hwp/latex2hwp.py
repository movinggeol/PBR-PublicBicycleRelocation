"""원고의 LaTeX 수식을 한글 수식 문법으로 바꾼다."""
import re

GREEK = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi pi rho sigma tau upsilon phi chi psi omega".split()
SYMBOLS = {
    "sum": "sum", "prod": "prod", "min": "min", "max": "max", "tanh": "tanh", "log": "log", "exp": "exp",
    "in": "in", "notin": "notin", "cup": "cup", "cap": "cap", "le": "le", "leq": "le", "ge": "ge", "geq": "ge",
    "ne": "!=", "neq": "!=", "times": "times", "cdot": "cdot", "rightarrow": "->", "to": "->", "leftarrow": "<-",
    "forall": "FORALL", "lceil": "lceil", "rceil": "rceil", "lfloor": "lfloor", "rfloor": "rfloor",
    "lvert": "|", "rvert": "|", "|": "||", "ell": "ell", "infty": "inf", "approx": "approx", "pm": "+-",
    "ldots": "cdots", "cdots": "cdots", "{": "lbrace", "}": "rbrace", "%": "%", "colon": ":",
}
SPACES = {"qquad": "~~~~", "quad": "~~", ",": "`", " ": "~", ";": "~", ":": "`", "!": ""}
SIZERS = {"big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr"}
TOKEN = re.compile(r"\\([A-Za-z]+|.)|([{}^_&])|(\s+)|(.)", re.S)


def tokenize(s):
    out = []
    for m in TOKEN.finditer(s):
        if m.group(1) is not None:
            out.append("\\" + m.group(1))
        elif m.group(2) is not None:
            out.append(m.group(2))
        elif m.group(3) is not None:
            out.append(" ")
        else:
            out.append(m.group(4))
    return out


class Conv:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self):
        self.skip_ws()
        return self.t[self.i] if self.i < len(self.t) else None

    def skip_ws(self):
        while self.i < len(self.t) and self.t[self.i] == " ":
            self.i += 1

    def take(self):
        self.skip_ws()
        tok = self.t[self.i]
        self.i += 1
        return tok

    def raw_group(self):
        """{...}의 원문을 그대로 돌려준다(\\text 안쪽)."""
        assert self.take() == "{"
        depth, parts = 1, []
        while True:
            tok = self.t[self.i]
            self.i += 1
            if tok == "{":
                depth += 1
            elif tok == "}":
                depth -= 1
                if depth == 0:
                    break
            parts.append(tok[1:] if tok.startswith("\\") and len(tok) == 2 and not tok[1].isalpha() else tok)
        return "".join(parts)

    def raw_text_group(self, src_text):
        return src_text

    def group(self):
        if self.peek() == "{":
            self.take()
            out = self.seq(stop="}")
            self.take()
            return "{" + out + "}"
        return "{" + self.atom() + "}"

    def delim(self):
        tok = self.take()
        if tok.startswith("\\"):
            name = tok[1:]
            return SYMBOLS.get(name, name)
        return tok

    def atom(self):
        tok = self.take()
        if not tok.startswith("\\"):
            if tok in "^_":
                return " " + tok + self.group()
            if tok == "&":
                return " & "
            if tok in "<>":
                return " " + tok + " "  # '<-'가 화살표로 읽히지 않게 띄운다
            if tok == "{":
                inner = self.seq(stop="}")
                self.take()
                return "{" + inner + "}"
            return tok
        name = tok[1:]
        if name in ("text", "operatorname", "mathrm", "textrm", "mbox"):
            return ' "' + self.raw_group() + '" '
        if name in ("mathcal", "mathbb", "mathbf", "boldsymbol", "mathit"):
            return self.group()
        if name in ("frac", "tfrac", "dfrac"):
            a = self.group()
            b = self.group()
            return " {" + a + " over " + b + "} "
        if name == "sqrt":
            return " sqrt " + self.group()
        if name == "hat":
            return " hat " + self.group()
        if name in ("overline", "bar"):
            return " bar " + self.group()
        if name == "left":
            return " LEFT " + self.delim() + " "
        if name == "right":
            return " RIGHT " + self.delim() + " "
        if name in SIZERS:
            return " " + self.delim() + " "
        if name == "begin":
            env = self.raw_group()
            body = self.seq(stop="\\end")
            self.take()
            self.raw_group()
            kw = {"cases": "cases", "aligned": "eqalign", "array": "pile"}[env]
            return " " + kw + "{" + body + "} "
        if name == "\\":
            # 줄 바꿈 뒤 [4pt] 같은 간격은 버린다
            if self.peek() == "[":
                while self.take() != "]":
                    pass
            return " # "
        if name in SPACES:
            return " " + SPACES[name] + " "
        if name in SYMBOLS:
            return " " + SYMBOLS[name] + " "
        if name.lower() in GREEK:
            return " " + (name.upper() if name[0].isupper() else name) + " "
        raise ValueError("모르는 명령: \\" + name)

    def seq(self, stop=None):
        out = []
        while self.i < len(self.t) and self.peek() != stop:
            out.append(self.atom())
        return "".join(out)


def convert(latex):
    """LaTeX 한 덩어리를 (한글 수식, 식 번호 또는 None)으로 바꾼다."""
    tag = None
    m = re.search(r"\\tag\{([^}]*)\}", latex)
    if m:
        tag = m.group(1)
        latex = latex[: m.start()] + latex[m.end():]
    out = Conv(tokenize(latex.strip())).seq()
    out = re.sub(r"\s+", " ", out).strip()
    return out, tag


def _top_level_split(latex, sep):
    r"""괄호 · \left/\right · 환경 밖에 있는 sep 명령에서 나눈다."""
    toks = re.findall(r"\\[A-Za-z]+|\\.|.", latex, re.S)
    parts, cur, depth = [], [], 0
    for t in toks:
        if t in ("{", r"\left", r"\begin"):
            depth += 1
        elif t in ("}", r"\right", r"\end"):
            depth -= 1
        if depth == 0 and t == sep:
            if sep == r"\rightarrow":
                cur.append(sep)
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(t)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def forms(latex):
    """줄 수식의 후보 모양 목록: (한글 수식, 글자 크기). 앞의 것일수록 바람직하다."""
    body = re.sub(r"\\tag\{[^}]*\}", "", latex)
    cands = [convert(body)[0]]
    if r"\begin{aligned}" in body:
        cands.append(convert(body.replace(r"\qquad", r"\\ &"))[0])
    for sep in (r"\qquad", r"\rightarrow"):
        parts = _top_level_split(body, sep)
        if len(parts) > 1:
            cands.append("pile{" + " # ".join(convert(p)[0] for p in parts) + "}")
    out = [(c, 10.0) for c in cands]
    out += [(cands[-1], 9.0), (cands[-1], 8.0)]
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="LaTeX 수식을 한글 수식 문법으로 바꿔 찍는다(확인용)")
    ap.add_argument("latex", nargs="+")
    for s in ap.parse_args().latex:
        print(convert(s))
