"""PyTorch əsasları: müraciət kateqoriyası klassifikatoru (tensor, autograd, training loop).

Embedding modelindən (bge-m3) alınan vektorlar üzərində kiçik MLP öyrədilir.
Bu model LLM-ə müraciət etmədən millisaniyələrlə kateqoriya təyin edə bilər
(məs: n8n-də e-poçtla gələn müraciətlərin avtomatik çeşidlənməsi üçün).

İstifadə:  python -m finetune.intent_classifier
"""
import json
from pathlib import Path

import torch
from torch import nn

from app.llm import get_embeddings

HERE = Path(__file__).parent
LABELS = ["complaint", "request", "suggestion", "information"]


class IntentMLP(nn.Module):
    def __init__(self, dim: int, n_classes: int):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(dim, 256), nn.ReLU(), nn.Dropout(0.2), nn.Linear(256, n_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def main() -> None:
    rows = [json.loads(l) for l in (HERE / "data" / "intents.jsonl").read_text("utf-8").splitlines() if l.strip()]
    emb = get_embeddings()
    X = torch.tensor(emb.embed_documents([r["text"] for r in rows]), dtype=torch.float32)
    y = torch.tensor([LABELS.index(r["label"]) for r in rows])

    perm = torch.randperm(len(X))
    split = int(len(X) * 0.8)
    tr, te = perm[:split], perm[split:]

    model = IntentMLP(X.shape[1], len(LABELS))
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-2)
    loss_fn = nn.CrossEntropyLoss()

    for epoch in range(100):
        model.train()
        opt.zero_grad()
        loss = loss_fn(model(X[tr]), y[tr])
        loss.backward()  # autograd qradiyentləri hesablayır
        opt.step()
        if epoch % 20 == 0:
            model.eval()
            with torch.no_grad():
                acc = (model(X[te]).argmax(1) == y[te]).float().mean().item() if len(te) else float("nan")
            print(f"epoch {epoch:3d}  loss={loss.item():.4f}  test_acc={acc:.2f}")

    torch.save({"state_dict": model.state_dict(), "dim": X.shape[1], "labels": LABELS}, HERE / "intent_mlp.pt")
    print("Model saxlanıldı: finetune/intent_mlp.pt")


if __name__ == "__main__":
    main()
