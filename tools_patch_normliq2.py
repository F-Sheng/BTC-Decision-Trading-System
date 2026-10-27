import ast

p = r"E:\btc-ai\vl_coin.py"
s = open(p, encoding="utf-8").read()

old = '''    # 英文单位
    m = re.match(r"([0-9.]+)\\s*([MKB]?)", s)
    if not m:
        return None
    f = float(m.group(1))
    unit = m.group(2)
    if unit == "K":
        f /= 1000
    elif unit == "B":
        f *= 1000
    return round(f, 2)'''

new = '''    # 英文单位
    m = re.match(r"([0-9.]+)\\s*([MKB]?)", s)
    if not m:
        return None
    f = float(m.group(1))
    unit = m.group(2)
    if unit == "K":
        f /= 1000
    elif unit == "B":
        f *= 1000
    f = round(f, 2)

    # ---- 合理性夹取（2026-10-27 新增）----
    # BTC 3% 窗口内的累计清算杠杆现实范围约 50M ~ 3000M。
    # 实测 VLM 会把 "5.5934亿" 读成 "559.34亿"（多两个数量级），结果变成 55934。
    if f > 5000:
        f = round(f / 100, 2)
    elif 0 < f < 1:
        f = round(f * 100, 2)   # 被误当 亿 处理过
    return f'''

assert old in s, "norm_liq 英文分支未匹配"
s = s.replace(old, new, 1)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("vl_coin.py: norm_liq 已加数量级夹取（>5000 视为 亿 误读，自动 /100）")

# 快速验证
import importlib.util
spec = importlib.util.spec_from_file_location("vlc", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
for v in ("559.34M", "5.5934亿", "559.34亿", "563.19M", "5.6319亿", "394.90M", "276万"):
    print("  %-12s -> %s" % (v, m.norm_liq(v)))
