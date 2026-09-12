# danmuji-tts

## 简介

朗读弹幕！

弹幕由 [Java版B站弹幕姬](https://github.com/BanqiJane/Bilibili_Danmuji/) 提供，TTS 由 [mimo-v2.5-tts](https://mimo.mi.com/models/zh-CN/mimo-v2.5-tts) 驱动。

## 已实现功能

- [x] 使用在线 TTS 服务（openai 兼容，流式）
- [x] 将 emoji 表情转换为中文
- [x] 使用 miniaudio 实现跨平台低延迟音频播放

## 使用方法

1. 参照 [Java版B站弹幕姬](https://github.com/BanqiJane/Bilibili_Danmuji/) 的说明，配置好弹幕姬；
2. 配置 Python 环境。`pip install -r requirements.txt`；
3. 修改 [bili_chat_tts.py](./bili_chat_tts.py) 中的配置，随后运行该脚本。
