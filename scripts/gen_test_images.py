"""生成图像匹配的验证素材。

要检验的不是「两张完全不同的图能不能区分」——那太容易了。
真正要证明的是：**同一件物品的两张不同照片**能被认出来，
而**同类不同件**的物品不会被错认。

所以每件物品生成两张「照片」：
    A 张给 LOST（用户以前拍的），B 张给 FOUND（工作人员拍的）
两张之间做了真实拍摄会有的变化：不同角度、不同裁切、不同亮度、加噪点、轻微旋转。

这些是合成图，不是真实照片。真实照片走完全相同的通道，
唯一区别是 CLIP 对真实照片的表征质量更高，只会更好不会更差。

    python -m scripts.gen_test_images --out /data/images/_testset
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# (key, 中文名, 底色, 配件色, 形状)
ITEMS = [
    ("backpack", "黒いリュック", (28, 28, 32), (200, 200, 205), "backpack"),
    ("red_backpack", "赤いリュック", (170, 40, 45), (240, 240, 240), "backpack"),
    ("wallet", "茶色い長財布", (110, 70, 45), (190, 160, 110), "wallet"),
    ("black_wallet", "黒い長財布", (32, 30, 30), (120, 120, 125), "wallet"),
    ("umbrella", "紺の折り畳み傘", (30, 45, 95), (215, 215, 220), "umbrella"),
    ("bottle", "青いステンレス水筒", (40, 95, 180), (225, 225, 230), "bottle"),
    ("phone", "黒いスマートフォン", (20, 20, 24), (90, 200, 255), "phone"),
]

CANVAS = 384


def _bg(draw: ImageDraw.ImageDraw, rng: random.Random) -> None:
    """桌面/地面背景，带一点渐变，避免纯色让 CLIP 只看形状。"""
    base = rng.randint(198, 226)
    for y in range(CANVAS):
        v = int(base - y * 0.06)
        draw.line([(0, y), (CANVAS, y)], fill=(v, v - 3, v - 8))


def _draw(shape: str, color, accent, rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (CANVAS, CANVAS))
    d = ImageDraw.Draw(img)
    _bg(d, rng)
    cx, cy = CANVAS // 2, CANVAS // 2

    if shape == "backpack":
        d.rounded_rectangle([cx - 78, cy - 96, cx + 78, cy + 104], 34, fill=color)
        d.rounded_rectangle([cx - 54, cy + 4, cx + 54, cy + 74], 18,
                            fill=tuple(max(0, c - 18) for c in color))
        d.arc([cx - 96, cy - 96, cx - 40, cy + 40], 250, 110, fill=accent, width=9)
        d.arc([cx + 40, cy - 96, cx + 96, cy + 40], 70, 290, fill=accent, width=9)
        d.ellipse([cx - 18, cy - 62, cx + 18, cy - 26], fill=accent)
    elif shape == "wallet":
        d.rounded_rectangle([cx - 104, cy - 58, cx + 104, cy + 58], 12, fill=color)
        d.line([(cx, cy - 58), (cx, cy + 58)], fill=tuple(max(0, c - 34) for c in color), width=4)
        d.rounded_rectangle([cx + 16, cy - 34, cx + 92, cy + 6], 4, fill=accent)
        d.rounded_rectangle([cx + 22, cy - 26, cx + 86, cy + 14], 4,
                            fill=tuple(min(255, a + 25) for a in accent))
    elif shape == "umbrella":
        d.pieslice([cx - 118, cy - 92, cx + 118, cy + 92], 180, 360, fill=color)
        for k in range(-3, 4):
            d.line([(cx, cy), (cx + k * 34, cy - 88)], fill=accent, width=3)
        d.line([(cx, cy - 4), (cx, cy + 108)], fill=(70, 55, 45), width=9)
        d.arc([cx - 34, cy + 84, cx + 2, cy + 122], 0, 180, fill=(70, 55, 45), width=9)
    elif shape == "bottle":
        d.rounded_rectangle([cx - 40, cy - 96, cx + 40, cy + 110], 24, fill=color)
        d.rounded_rectangle([cx - 26, cy - 128, cx + 26, cy - 88], 12, fill=accent)
        d.rounded_rectangle([cx - 40, cy + 6, cx + 40, cy + 34], 6,
                            fill=tuple(min(255, c + 40) for c in color))
    elif shape == "phone":
        d.rounded_rectangle([cx - 52, cy - 100, cx + 52, cy + 100], 18, fill=color)
        d.rounded_rectangle([cx - 44, cy - 90, cx + 44, cy + 88], 12, fill=accent)
        d.ellipse([cx - 40, cy - 84, cx - 18, cy - 62], fill=(30, 30, 34))
    return img


def _photo(img: Image.Image, rng: random.Random, variant: str) -> Image.Image:
    """把「示意图」变成「照片」：角度、裁切、亮度、噪点都不一样。"""
    angle = rng.uniform(-14, 14) if variant == "b" else rng.uniform(-5, 5)
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(210, 207, 202))

    if variant == "b":
        m = rng.randint(24, 52)
        img = img.crop((m, m, CANVAS - m, CANVAS - m)).resize((CANVAS, CANVAS), Image.BICUBIC)

    factor = rng.uniform(0.78, 0.94) if variant == "b" else rng.uniform(0.98, 1.12)
    img = Image.eval(img, lambda v: max(0, min(255, int(v * factor))))

    px = img.load()
    for _ in range(int(CANVAS * CANVAS * 0.02)):
        x, y = rng.randrange(CANVAS), rng.randrange(CANVAS)
        r, g, b = px[x, y]
        n = rng.randint(-22, 22)
        px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))

    return img.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.9)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/data/images/_testset")
    ap.add_argument("--seed", type=int, default=20260828)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for key, label, color, accent, shape in ITEMS:
        for variant in ("a", "b"):
            # 同一物品的两张照片用不同随机种子，保证差异真实存在
            rng = random.Random(f"{args.seed}-{key}-{variant}")
            img = _photo(_draw(shape, color, accent, rng), rng, variant)
            path = out / f"{key}_{variant}.jpg"
            img.save(path, quality=88)
            print(f"  {path.name:<24} {label}")

    print(f"\n共 {len(ITEMS) * 2} 张，输出到 {out}")
    print("a = 用户以前拍的（LOST）， b = 工作人员拍的（FOUND）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
