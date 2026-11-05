# 第三方组件与模型说明

本仓库只发布“声章 · 小说讲书工作台”的应用代码和文档，不包含模型权重、Python 虚拟环境、生成音频或用户小说。

## Breeze TTS 2

应用通过本机安装的 Breeze TTS 2 设计固定角色声音并生成对白。Breeze 源码、模型权重和推理依赖需要按照上游项目的许可单独获取：

- 上游项目：[breezeblue-ai/breeze-tts](https://github.com/breezeblue-ai/breeze-tts)
- 本地目录约定：`runtimes/breeze-tts`、`models/breeze-tts-2`
- 使用前请阅读上游 `LICENSE` 和模型卡片，确认你的用途符合许可。

当前项目的默认设置按研究和非商业用途编写。商用前请分别确认 Breeze、模型、音效和小说文本的授权。

## IndexTTS

IndexTTS 是本机已有的另一个项目。本应用不会重复安装或上传 IndexTTS，也不会在 Breeze 流程失败时静默切换到其他引擎。

## 音效与小说内容

内置音效仅作为流程示例。导入的小说、参考音频和外部音效由使用者负责版权、隐私和传播授权。
