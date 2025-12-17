import pandas as pd
from collections import defaultdict
import json

def tree():
    return defaultdict(tree)

def build_tree(df):
    root = tree()
    for _, row in df.iterrows():
        levels = [l for l in row if pd.notnull(l)]
        current = root
        for lvl in levels:
            current = current[lvl]
    return root

def to_list(d):
    result = []
    for k, v in d.items():
        result.append({
            "name": k,
            "children": to_list(v)
        })
    return result

df = pd.read_excel("申万行业分类.xlsx")
t = build_tree(df)
data = to_list(t)

with open("industry_tree.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
