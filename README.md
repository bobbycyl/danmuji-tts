# danmuji-tts

## 简介

朗读弹幕！

弹幕由 [Java版B站弹幕姬](https://github.com/BanqiJane/Bilibili_Danmuji/) 提供，TTS 由 [Kokoro](https://github.com/hexgrad/kokoro) 驱动。

## 已实现功能

- [x] 朗读中英文混合弹幕
- [x] 将 emoji 表情转换为中文
- [x] 使用 pygame 实现跨平台低延迟音频播放

## 使用方法

1. 参照 [Java版B站弹幕姬](https://github.com/BanqiJane/Bilibili_Danmuji/) 的说明，配置好弹幕姬；
2. 配置 Python 环境。`pip install -r requirements.txt`；
3. 修改 [bili_chat_tts.py](./bili_chat_tts.py) 中的配置，随后运行该脚本。

## 一些碎碎念

**Q1** 为什么不用 `sounddevice`、`pyglet` 或 `miniaudio`？

**A1** `sounddevice` 在 `pipewire` 下表现很差，会让全局音频延迟变高。后两者则是配置和使用都不如 `pygame` 方便。
