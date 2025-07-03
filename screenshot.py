import sys
from pathlib import Path

import cv2
import numpy as np
import requests


def get_and_convert_screenshot(output_file=None, width=720, height=1280):
    response = requests.get('http://127.0.0.1:20165/screenshot?format=png', stream=True)
    if response.status_code != 200:
        raise Exception(f'Failed to fetch screenshot: {response.status_code}')

    # Read raw bytes
    data = response.raw.read()

    # Convert from RGB565
    arr = np.frombuffer(data, dtype=np.uint16)
    arr = arr.reshape((height, width))

    b = cv2.bitwise_and(arr, 0b1111100000000000)
    b = cv2.convertScaleAbs(b, alpha=0.00390625)
    m = cv2.convertScaleAbs(b, alpha=0.03125)
    cv2.add(b, m, dst=b)

    g = cv2.bitwise_and(arr, 0b0000011111100000)
    g = cv2.convertScaleAbs(g, alpha=0.125)
    m = cv2.convertScaleAbs(g, alpha=0.015625)
    cv2.add(g, m, dst=g)

    r = cv2.bitwise_and(arr, 0b0000000000011111)
    r = cv2.convertScaleAbs(r, alpha=8)
    m = cv2.convertScaleAbs(r, alpha=0.03125)
    cv2.add(r, m, dst=r)

    if output_file is None:
        output_file = str(Path.home() / 'Downloads' / 'screenshot.png')

    image = cv2.merge([r, g, b])
    cv2.imwrite(output_file, image)


if __name__ == '__main__':
    output_path = sys.argv[1] if len(sys.argv) > 1 else None
    get_and_convert_screenshot(output_path)
