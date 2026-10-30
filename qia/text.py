"""Tokenizer + BM25, small enough to explain line by line."""
import math, re
from collections import Counter

STOP = set("""a an the of to in on for and or is are was were be been by with at from as that this these those it its
what which who how when where why does do did can could will would my i we you they me our your car vehicle
there any all have has had not no so if then than into about after before while also just very
january february march april may june july august september october november december
time available still back told said stated contact contacted owner owns would one get got been""".split())
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?")

def tokenize(text):
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP and len(t) > 1]

class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d)) for d in docs]
        self.lens = [sum(t.values()) for t in self.tfs]
        self.avgdl = sum(self.lens) / max(len(self.lens), 1)
        df = Counter(t for tf in self.tfs for t in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query):
        q = tokenize(query)
        out = []
        for tf, dl in zip(self.tfs, self.lens):
            s = 0.0
            for t in q:
                if t in tf:
                    s += self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out.append(s)
        return out
