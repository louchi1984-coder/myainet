# 模型推荐矩阵（按硬件分档）

## LLM 推理（Ollama / llama.cpp / vLLM）

| VRAM / RAM 条件 | 推荐模型 | 量化格式 | 备注 |
|----------------|---------|---------|------|
| VRAM ≥ 80GB | Llama 3.1 405B、Qwen2.5-72B-FP16 | FP16/BF16 | 完整精度，顶级效果 |
| VRAM 48–79GB | DeepSeek-R1 70B、Llama 3.3 70B | Q8_0 | 接近满精度 |
| VRAM 24–47GB | Qwen2.5-72B、DeepSeek-R1 32B | Q4_K_M | 日常主力 |
| VRAM 16–23GB | Qwen2.5-32B、Mistral Large 2 | Q4_K_M | 均衡性能 |
| VRAM 12–15GB | Llama 3.2 11B、Qwen2.5-14B | Q6_K | 良好效果 |
| VRAM 8–11GB | Qwen2.5-7B、Gemma 2 9B、Phi-3.5 Mini | Q8_0 | 日常够用 |
| VRAM 6–7GB | Llama 3.2 3B、Qwen2.5-7B | Q4_K_M | 轻量 |
| VRAM 4–5GB | Phi-3.5 Mini、TinyLlama | Q4_0 | 非常有限 |
| VRAM < 4GB | 不推荐本地 LLM，建议 API | - | 考虑 API 方案 |
| RAM ≥ 64GB 纯CPU | Llama 3.1 8B GGUF、Qwen2.5-7B GGUF | Q8_0 | 速度慢（~5 tok/s） |
| RAM 32–63GB 纯CPU | Qwen2.5-7B、Phi-3.5 Mini | Q4_K_M | 较慢（~2 tok/s） |
| Apple M3 Max/Ultra (≥36GB UM) | Llama 3.3 70B、Qwen2.5-72B | Q4_K_M | Metal 加速，性能佳 |
| Apple M2/M3 Pro (18–36GB UM) | Qwen2.5-14B、Llama 3.1 8B | Q6_K | 日常可用 |
| Apple M1/M2 Base (8–16GB UM) | Qwen2.5-7B、Phi-3.5 Mini | Q4_K_M | 够用 |

## 图像生成（ComfyUI / AUTOMATIC1111）

| GPU 条件 | 推荐模型 | 备注 |
|---------|---------|------|
| VRAM ≥ 24GB | SDXL FP16、Flux.1-dev FP8 | 高分辨率无压力 |
| VRAM 12–23GB | SDXL、Flux.1-schnell Q8 | 主流方案 |
| VRAM 8–11GB | SDXL（xFormers）、SD 1.5 | 需开 xFormers |
| VRAM 6–7GB | SD 1.5 FP16 | 控制分辨率 ≤1024 |
| VRAM 4–5GB | SD 1.5 Q8，–lowvram 模式 | 较慢 |
| Apple Silicon ≥ 16GB UM | SDXL、SD 1.5（Core ML） | Draw Things / Diffusers |

## 模型微调（LoRA / QLoRA）

| VRAM 条件 | 推荐方案 | 可微调模型规模 |
|---------|---------|-------------|
| VRAM ≥ 80GB | 全量微调（Full FT）| 70B |
| VRAM 48–79GB | Full FT / QLoRA | 70B |
| VRAM 24–47GB | QLoRA + Unsloth | 34B |
| VRAM 16–23GB | QLoRA + Unsloth | 13B–14B |
| VRAM 12–15GB | QLoRA (4-bit) | 7B–8B |
| VRAM 8–11GB | QLoRA (4-bit) | 3B–7B |
| VRAM < 8GB | 不推荐微调（考虑用 API 微调服务）| - |
