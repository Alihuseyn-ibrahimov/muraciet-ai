# DigitalOcean-a deploy

1. **Droplet yarat**: Ubuntu 24.04, ən azı 4 GB RAM (Ollama embeddings üçün; tam lokal LLM üçün 16 GB+ və ya GPU Droplet).
2. Serverdə:

```bash
curl -fsSL https://get.docker.com | sh
git clone <repo-url> muraciet-ai && cd muraciet-ai
cp .env.example .env && nano .env        # API açarları, ADMIN_API_KEY, TELEGRAM_BOT_TOKEN
docker compose up -d --build
docker compose exec ollama ollama pull bge-m3
docker compose exec api python -m app.rag.ingest
```

3. **HTTPS (Caddy)** — `/etc/caddy/Caddyfile`:

```
muraciet.example.az {
    reverse_proxy localhost:8000
}
n8n.example.az {
    reverse_proxy localhost:5678
}
```

4. **Firewall**: yalnız 22, 80, 443 açıq saxlayın (`ufw allow OpenSSH && ufw allow 80,443/tcp && ufw enable`).
   docker-compose-dakı `ports` xəritələrini `127.0.0.1:8000:8000` formatına dəyişin ki, xidmətlər
   birbaşa internetə açılmasın.
5. **Backup**: `pg_dump` ilə gündəlik cron + DigitalOcean Spaces-ə yükləmə.
6. **Monitorinq**: `LANGSMITH_TRACING=true` — hər dialoqun agent addımlarını, gecikməni və xərci izləyin.
