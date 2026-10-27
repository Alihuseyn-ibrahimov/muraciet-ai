# Fine-tune olunmuş modeli NVIDIA GPU-da serve etmək (vLLM)

```bash
pip install -r finetune/requirements.txt

# 1. Təlim (QLoRA)
python -m finetune.train_qlora --base Qwen/Qwen2.5-7B-Instruct --epochs 3

# 2. vLLM ilə OpenAI-uyğun API (LoRA adapter birbaşa yüklənir)
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --enable-lora \
  --lora-modules muraciet-az=finetune/outputs/muraciet-lora \
  --port 8001 --max-model-len 8192 --gpu-memory-utilization 0.9

# 3. Sürəti ölç
python -m finetune.benchmark --url http://localhost:8001/v1 --model muraciet-az --concurrency 8
```

## Sistemə qoşmaq

`.env`:

```
LLM_PROVIDER=vllm
LLM_MODEL=muraciet-az
OPENAI_BASE_URL=http://gpu-server:8001/v1
```

Kodda heç nə dəyişmir — `app/llm.py` vLLM-i OpenAI klienti ilə çağırır.
vLLM-də tool calling üçün `--enable-auto-tool-choice --tool-call-parser hermes` əlavə edin
(Qwen2.5 modelləri hermes formatını dəstəkləyir).

## Fine-tune-dan sonra mütləq

`python -m eval.run_eval` işə salın və nəticəni baza modeli (Claude/GPT) ilə müqayisə edin.
Routing accuracy və judge score aşağı düşübsə, fine-tune olunmuş modeli yalnız FAQ/chitchat
agentlərində, tool calling-i isə güclü modeldə saxlamaq olar.
