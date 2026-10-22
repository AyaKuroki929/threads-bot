from PIL import Image, ImageDraw, ImageFont

SRC = "current_menu.png"
OUT = "new_menu.png"
W, H = 2500, 1686
BG = (254, 249, 246)
ACCENT = (254, 97, 78)
WHITE = (255, 255, 255)

src = Image.open(SRC).convert("RGB")

def grab(box, scale):
    part = src.crop(box)
    w, h = part.size
    return part.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

SCALE = 0.80
left_art  = grab((60, 225, 1240, 1042), SCALE)
left_txt  = grab((60, 1075, 1240, 1433), SCALE)
right_art = grab((1260, 225, 2440, 1042), SCALE)
right_txt = grab((1260, 1075, 2440, 1433), SCALE)

canvas = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(canvas)

# 外枠（角丸・元と同じ太さ）
d.rounded_rectangle([12, 12, W - 13, H - 13], radius=46, outline=ACCENT, width=26)

ART_TOP = 80
GAP = 34
for art, txt, cx in ((left_art, left_txt, 625), (right_art, right_txt, 1875)):
    canvas.paste(art, (cx - art.width // 2, ART_TOP))
    canvas.paste(txt, (cx - txt.width // 2, ART_TOP + art.height + GAP))

# 中央の仕切り線（元画像と同じ細い縦線）
d.line([(1250, 120), (1250, 1110)], fill=ACCENT, width=5)

# 下段の帯
BAR = [78, 1152, W - 79, 1572]
d.rounded_rectangle(BAR, radius=40, fill=ACCENT)

FONT = "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc"
LABEL = "とうこさんって何？"
size = 168
while size > 60:  # 帯に収まる最大サイズまで落とす
    f = ImageFont.truetype(FONT, size)
    l, t, r, b = d.textbbox((0, 0), LABEL, font=f)
    if r - l <= (BAR[2] - BAR[0]) - 260 and b - t <= (BAR[3] - BAR[1]) - 110:
        break
    size -= 4
cx = (BAR[0] + BAR[2]) / 2
cy = (BAR[1] + BAR[3]) / 2
d.text((cx - (r + l) / 2, cy - (b + t) / 2), LABEL, font=f, fill=WHITE)

canvas.save(OUT, quality=92)
print("saved", OUT, canvas.size, "font size", size)
