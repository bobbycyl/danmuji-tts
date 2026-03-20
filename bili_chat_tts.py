import re
import sys
import threading
import time
from queue import Queue
from random import choice

import emoji
import numpy as np
import orjson
import pygame
import torch
from kokoro import KModel, KPipeline
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

# --- 配置区 ---
URI = "ws://localhost:23333/danmu/sub"
MAX_QUEUE_SIZE = 10
VOICE_CODES = [
    "zf_022",
    # "zf_059",
    # "zf_004",
    # "zf_019",
    # "zf_023",
    # "zf_039",
    # "zf_049",
    # "zf_051",
    # "zf_067",
    # "zf_083",
    # "zf_094",
]
REPO_ID = "hexgrad/Kokoro-82M-v1.1-zh"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
# --- 配置区结束 ---

pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)

# 为了支持中英混读，需要先定义一个英文 G2P 解析器
en_pipeline = KPipeline(lang_code="a", repo_id=REPO_ID, model=False)


def en_callable(text):
    # 这里处理一些特定词汇的特殊发音，或者直接返回英文音标
    if text.lower() == "kokoro":
        return "kˈOkəɹO"
    # 把英文单词转为音标
    try:
        return next(en_pipeline(text)).phonemes
    except:
        return ""


# 初始化模型并载入中文 Pipeline，同时挂载 en_callable
model = KModel(repo_id=REPO_ID).to(DEVICE).eval()
pipeline = KPipeline(
    lang_code="z", repo_id=REPO_ID, model=model, en_callable=en_callable
)

danmaku_queue = Queue(maxsize=MAX_QUEUE_SIZE)


def speed_callable(len_ps):
    speed = 0.8
    if len_ps <= 83:
        speed = 1
    elif len_ps < 183:
        speed = 1 - (len_ps - 83) / 500
    return speed * 1.1


def tts_worker():
    print("TTS 播放线程已启动...")
    while True:
        text = danmaku_queue.get()
        try:
            generator = pipeline(text, voice=choice(VOICE_CODES), speed=speed_callable)
            for _, _, audio in generator:
                if audio is not None:
                    if isinstance(audio, torch.Tensor):
                        audio = audio.detach().cpu().numpy()
                    elif isinstance(audio, str):
                        raise ValueError(
                            "音频数据应为 numpy 数组或 torch.Tensor，而不是字符串"
                        )
                    if audio.dtype == np.float32:
                        audio_int16 = (audio * 32767).astype(np.int16)
                    else:
                        audio_int16 = audio.astype(np.int16)
                    sound = pygame.sndarray.make_sound(audio_int16)
                    channel = sound.play()
                    while channel.get_busy():
                        pygame.time.wait(10)
        except Exception as e:
            print(f"播放出错: {e}")


def input_listener():
    emoji.config.load_language("zh")
    try:
        with connect(URI) as websocket:
            print(f"已连接到: {URI}")

            while True:
                try:
                    message = websocket.recv()
                    msg = orjson.loads(message)
                    # print(msg)
                    if msg["cmd"] in ["danmu", "superchat"]:
                        danmaku_queue.put(
                            msg["result"]["uname"]
                            + "他说："
                            + emoji.demojize(msg["result"]["msg"], ("[", "]"), "zh")
                        )
                    elif msg["cmd"][:7].lower() == "welcome":
                        danmaku_queue.put("欢迎 " + msg["result"]["uname"])
                except ConnectionClosed:
                    print("连接已关闭")
                    break

    except Exception as e:
        print(f"连接发生错误: {e}")


if __name__ == "__main__":
    t = threading.Thread(target=tts_worker, daemon=True)
    t.start()
    try:
        danmaku_queue.put("弹幕朗读已启动！")
        input_listener()
    except KeyboardInterrupt:
        print("\n已停止")
