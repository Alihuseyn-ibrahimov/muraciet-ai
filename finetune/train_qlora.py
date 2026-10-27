"""QLoRA fine-tuning: kiçik açıq modeli təşkilatın rəsmi üslubuna və Azərbaycan dilinə uyğunlaşdırmaq.

Məqsəd: data suverenliyi tələb edən dövlət qurumları üçün xarici API-yə məlumat göndərmədən
öz serverində işləyən model. Nəticə vLLM ilə serve olunur (serve.md) və sistemdə
LLM_PROVIDER=vllm ilə istifadə edilir.

Tələblər: NVIDIA GPU (≥16 GB VRAM), pip install -r finetune/requirements.txt
İstifadə:  python -m finetune.train_qlora --base Qwen/Qwen2.5-7B-Instruct --epochs 3
"""
import argparse
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer

HERE = Path(__file__).parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen2.5-7B-Instruct")
    ap.add_argument("--data", default=str(HERE / "data" / "sft_train.jsonl"))
    ap.add_argument("--out", default=str(HERE / "outputs" / "muraciet-lora"))
    ap.add_argument("--epochs", type=int, default=3)
    args = ap.parse_args()

    # 4-bit kvantlaşdırma (QLoRA) — 7B model ~6 GB VRAM-a sığır
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForCausalLM.from_pretrained(args.base, quantization_config=bnb, device_map="auto")
    model = prepare_model_for_kbit_training(model)

    lora = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        task_type="CAUSAL_LM",
    )

    dataset = load_dataset("json", data_files=args.data, split="train")

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=dataset,
        peft_config=lora,
        args=SFTConfig(
            output_dir=args.out,
            num_train_epochs=args.epochs,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=8,
            learning_rate=2e-4,
            lr_scheduler_type="cosine",
            warmup_ratio=0.05,
            logging_steps=10,
            save_strategy="epoch",
            bf16=True,
            gradient_checkpointing=True,
            report_to="none",
        ),
    )
    trainer.train()
    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"LoRA adapter saxlanıldı: {args.out}")


if __name__ == "__main__":
    main()
