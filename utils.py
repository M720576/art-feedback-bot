# utils.py
# Подготовка изображения: даунскейл и сохранение в JPEG
from PIL import Image
from io import BytesIO

def downscale(image_bytes: bytes, max_side: int = 1536, quality: int = 90) -> bytes:
    """
    Уменьшает изображение до максимальной стороны max_side (если нужно)
    и сохраняет в JPEG хорошего качества.

    Зачем:
    - Меньше пикселей = меньше токенов = дешевле и быстрее.
    - 1536 px по длинной стороне — нормальный баланс качество/цена для MVP.

    Возвращает: байты JPEG.
    """
    im = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = im.size

    # Считаем, нужно ли уменьшать
    longest = max(w, h)
    scale = longest / max_side if longest > max_side else 1.0

    if scale > 1.0:
        new_w = int(w / scale)
        new_h = int(h / scale)
        im = im.resize((new_w, new_h), Image.LANCZOS)

    out = BytesIO()
    im.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()
