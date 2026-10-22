import base64
import os
import queue
import threading

import emoji
import miniaudio
import numpy as np
import orjson
import requests
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import reconnect

# --- 配置区 ---
URI = "ws://localhost:23333/danmu/sub"

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_FORMAT = miniaudio.SampleFormat.SIGNED16
BUFFER_MSEC = 60
TEXT_QUEUE_MAX = 20
PCM_QUEUE_MAX = 200
MIMO_URL = "https://api.xiaomimimo.com/v1/chat/completions"
# --- 配置区结束 ---

danmaku_queue = queue.Queue(maxsize=TEXT_QUEUE_MAX)


def put_drop_oldest(q: queue.Queue, item) -> None:
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


# =========================================================
# 1. TTS 客户端
# =========================================================
class MimoTTS:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "mimo-v2.5-tts",
        voice: str = "mimo_default",
    ):
        self.api_key = api_key or os.environ["MIMO_API_KEY"]
        self.model = model
        self.voice = voice
        self._session = requests.Session()
        self._session.headers.update(
            {
                "api-key": self.api_key,
                "Content-Type": "application/json",
            }
        )

    def _body(self, text: str, stream: bool) -> dict:
        return {
            "model": self.model,
            "messages": [
                {"role": "user", "content": "用播音腔朗读直播间弹幕"},
                {"role": "assistant", "content": text},
            ],
            "audio": {
                "format": "pcm" if stream else "wav",
                "voice": self.voice,
            },
            "stream": stream,
        }

    # ---- 流式：逐块 yield int16 mono ndarray ----
    def stream_pcm16(self, text: str):
        with self._session.post(
            MIMO_URL, json=self._body(text, True), stream=True, timeout=60
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = orjson.loads(payload)
                except orjson.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                audio = delta.get("audio") or {}
                b64 = audio.get("data")
                if not b64:
                    continue
                pcm_bytes = base64.b64decode(b64)
                arr = np.frombuffer(pcm_bytes, dtype="<i2")
                if arr.size:
                    yield arr

    # ---- 非流式（备用，不打断的简单播放）----
    def synthesize_pcm16(self, text: str) -> np.ndarray:
        r = self._session.post(MIMO_URL, json=self._body(text, False), timeout=60)
        r.raise_for_status()
        b64 = r.json()["choices"][0]["message"]["audio"]["data"]
        raw = base64.b64decode(b64)
        decoded = miniaudio.decode(
            raw,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=SAMPLE_RATE,
        )
        return np.frombuffer(decoded.samples, dtype=np.int16)


# =========================================================
# 2. 播放器
# =========================================================
class MimoReader:
    """
    文本 -> 朗读。
    - speak(text): 入队，非阻塞。
    - stop(): 立即打断当前朗读并清空队列。
    - 合成线程串行处理文本队列，流式 PCM 塞入 _pcm_q。
    - miniaudio generator 在音频线程从 _pcm_q 拉数据；空则输出静音。
    """

    def __init__(self, tts: MimoTTS, sample_rate: int = SAMPLE_RATE):
        self.tts = tts
        self.sr = sample_rate

        self._text_q: "queue.Queue[str | None]" = queue.Queue(maxsize=TEXT_QUEUE_MAX)
        self._pcm_q: "queue.Queue[bytes]" = queue.Queue(maxsize=PCM_QUEUE_MAX)

        # 预生成静音块（10ms），队列空时喂给设备
        silence_frames = max(1, self.sr // 100)
        self._silence = np.zeros(silence_frames, dtype=np.int16).tobytes()

        # 设备
        self._device = miniaudio.PlaybackDevice(
            output_format=SAMPLE_FORMAT,
            nchannels=CHANNELS,
            sample_rate=self.sr,
            buffersize_msec=BUFFER_MSEC,
            backends=[],  # 空=自动选
            app_name="mimo-reader",
        )

        # 合成线程
        self._synth_thread = threading.Thread(
            target=self._synth_loop, daemon=True, name="mimo-synth"
        )
        self._synth_thread.start()

        # 启动音频（generator 在音频线程里跑）
        gen = self._gen_pcm()
        next(gen)
        self._device.start(gen)

    def speak(self, text: str):
        if text and text.strip():
            put_drop_oldest(self._text_q, text)

    def _gen_pcm(self):
        """
        与 miniaudio PlaybackDevice 的约定：
        - 主线程先 next(self._gen_pcm()) 一次，启动 generator
        - 之后每次 send(framecount)：必须 yield 不超过 framecount 帧的 bytes
        - int16 mono：1 帧 = 2 字节
        """
        # 预生成 10ms 静音，用于补空
        silence_10ms = np.zeros(self.sr // 100, dtype=np.int16).tobytes()

        pending = bytearray()  # 攒着还没消费的 PCM bytes
        frame_bytes = CHANNELS * 2  # 1 帧 = nchannels * int16

        framecount = yield b""  # 第一次 next()：占位返回

        while True:
            need_bytes = framecount * frame_bytes

            # 攒够 need_bytes
            while len(pending) < need_bytes:
                try:
                    block = self._pcm_q.get(timeout=0.02)
                    pending.extend(block)
                except queue.Empty:
                    # 补静音，保证不 underrun
                    pending.extend(silence_10ms)

            # 取出恰好 need_bytes
            out = bytes(pending[:need_bytes])
            del pending[:need_bytes]

            # 长度不足，补齐静音
            if len(out) < need_bytes:
                out = out + b"\x00" * (need_bytes - len(out))

            framecount = yield out

        self._gen_done.set()

    def _synth_loop(self):
        while True:
            try:
                text = self._text_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None:
                break

            try:
                for pcm in self.tts.stream_pcm16(text):
                    try:
                        self._pcm_q.put(pcm.tobytes(), timeout=0.5)
                    except queue.Full:
                        # 队列满：丢这一块，避免阻塞合成
                        pass
            except Exception as e:
                print(f"[mimo-reader] 合成失败: {e}")


def tts_worker():
    tts = MimoTTS()
    reader = MimoReader(tts)
    print("TTS 播放线程已启动...")
    while True:
        text = danmaku_queue.get()
        try:
            reader.speak(text)
        except Exception as e:
            print(f"播放出错: {e}")


def input_listener():
    emoji.config.load_language("zh")
    try:
        # reconnect 会在断链后自动重连（指数退避），替代一次性 connect
        for websocket in reconnect(URI):
            with websocket:
                print(f"已连接到: {URI}")

                while True:
                    try:
                        message = websocket.recv()
                    except ConnectionClosed:
                        print("连接已关闭，正在重连...")
                        break

                    try:
                        msg = orjson.loads(message)
                        print(msg)
                        is_chat = False
                        prefix = ""

                        if msg["cmd"] == "danmu":
                            is_chat = True
                            prefix = " 他说: "
                        elif msg["cmd"] == "gift":
                            is_chat = True
                            prefix = " 赠送了礼物: "
                        elif msg["cmd"] == "superchat":
                            is_chat = True
                            prefix = " 发送了醒目留言: "

                        if is_chat:
                            put_drop_oldest(
                                danmaku_queue,
                                msg["result"]["uname"]
                                + prefix
                                + emoji.demojize(
                                    msg["result"]["msg"], ("[", "]"), "zh"
                                ),
                            )
                        elif msg["cmd"][:7].lower() == "welcome":
                            put_drop_oldest(
                                danmaku_queue, "欢迎 " + msg["result"]["uname"]
                            )
                        elif msg["cmd"] == "follow":
                            put_drop_oldest(
                                danmaku_queue, msg["result"]["uname"] + "关注了直播间"
                            )
                    except Exception as e:
                        # 单条消息解析/字段异常不断链，跳过这条继续收
                        print(f"处理消息出错: {e}")

    except Exception as e:
        print(f"连接发生错误: {e}")


if __name__ == "__main__":
    t = threading.Thread(target=tts_worker, daemon=True)
    t.start()
    try:
        put_drop_oldest(danmaku_queue, "弹幕朗读已启动！")
        input_listener()
    except KeyboardInterrupt:
        print("\n已停止")
