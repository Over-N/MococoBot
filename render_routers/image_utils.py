from io import BytesIO

from PIL import Image


def decode_image(content: bytes) -> Image.Image:
    return Image.open(BytesIO(content)).convert("RGBA")
