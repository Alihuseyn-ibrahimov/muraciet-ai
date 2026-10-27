#!/bin/sh
# Bazanın şifrəli ehtiyat nüsxəsi (docker-compose.yml: backup servisi, postgres image-i ilə işləyir).
#
#   backup.sh once      bir nüsxə: pg_dump -Fc | openssl AES-256 (parol: BACKUP_PASSPHRASE_FILE) → $BACKUP_DIR
#   backup.sh daemon    hər gün BACKUP_HOUR-da (Asia/Baku) bir nüsxə, BACKUP_KEEP_DAYS-dən köhnələri silinir
#   backup.sh verify    ən son nüsxəni müvəqqəti bazaya bərpa edib cədvəllərin sətir sayını canlı baza ilə
#                       müqayisə edir, sonra müvəqqəti bazanı silir (bərpa sınağı)
#
# Nüsxədə müraciətlərin mətni açıq formadadır (FİN/telefon/ünvan bazada onsuz da Fernet ilə şifrəlidir), ona
# görə fayl bütövlükdə şifrələnir. Parol və DATA_ENCRYPTION_KEYS serverdən kənarda ayrıca saxlanılmalıdır:
# biri olmadan nüsxə, digəri olmadan şəxsi məlumat oxunmaz.
set -eu

: "${PGHOST:=postgres}" "${PGUSER:=muraciet}" "${PGDATABASE:=muraciet_v2}"
: "${BACKUP_DIR:=/backups}" "${BACKUP_KEEP_DAYS:=14}" "${BACKUP_HOUR:=2}"
: "${BACKUP_PASSPHRASE_FILE:=/run/secrets/backup_passphrase}"
export PGHOST PGUSER PGDATABASE
if [ -z "${PGPASSWORD:-}" ] && [ -f /run/secrets/postgres_password ]; then
    PGPASSWORD=$(cat /run/secrets/postgres_password)
fi
export PGPASSWORD
export TZ=Asia/Baku

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $*"; }

require_passphrase() {
    if [ ! -s "$BACKUP_PASSPHRASE_FILE" ]; then
        log "XƏTA: parol faylı yoxdur və ya boşdur ($BACKUP_PASSPHRASE_FILE)"
        exit 1
    fi
}

once() {
    require_passphrase
    mkdir -p "$BACKUP_DIR"
    name="$PGDATABASE-$(date '+%Y%m%d-%H%M%S').dump.enc"
    tmp="$BACKUP_DIR/.$name.part"
    # pipefail POSIX sh-da yoxdur: pg_dump-ın xətası ayrıca faylla yoxlanılır
    status="$BACKUP_DIR/.pg_dump.status"
    rm -f "$status"
    { pg_dump -Fc "$PGDATABASE" || echo failed > "$status"; } \
        | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$BACKUP_PASSPHRASE_FILE" -out "$tmp"
    if [ -f "$status" ]; then
        rm -f "$tmp" "$status"
        log "XƏTA: pg_dump alınmadı"
        exit 1
    fi
    mv "$tmp" "$BACKUP_DIR/$name"
    (cd "$BACKUP_DIR" && sha256sum "$name" > "$name.sha256")
    log "hazırdır: $name ($(du -h "$BACKUP_DIR/$name" | cut -f1))"
    find "$BACKUP_DIR" -maxdepth 1 -name "$PGDATABASE-*.dump.enc*" -mtime +"$BACKUP_KEEP_DAYS" -print -delete \
        | sed 's/^/köhnə nüsxə silindi: /'
}

latest() {
    ls -1t "$BACKUP_DIR"/"$PGDATABASE"-*.dump.enc 2>/dev/null | head -n 1
}

counts() {  # bazadakı bütün cədvəllərin dəqiq sətir sayı: "cədvəl sayı" sətirləri
    psql -d "$1" -XAt -F ' ' -c "SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name" |
    while read -r table; do
        echo "$table $(psql -d "$1" -XAtc "SELECT count(*) FROM \"$table\"")"
    done
}

verify() {
    require_passphrase
    file="${1:-$(latest)}"
    if [ -z "$file" ] || [ ! -f "$file" ]; then
        log "XƏTA: nüsxə tapılmadı"
        exit 1
    fi
    (cd "$(dirname "$file")" && sha256sum -c "$(basename "$file").sha256" >/dev/null) || {
        log "XƏTA: yoxlama cəmi uyğun deyil: $file"
        exit 1
    }
    target="${PGDATABASE}_restore_check"
    psql -d postgres -XAqc "DROP DATABASE IF EXISTS $target" -c "CREATE DATABASE $target"
    # müvəqqəti baza istənilən halda silinir
    trap 'psql -d postgres -XAqc "DROP DATABASE IF EXISTS $target" >/dev/null 2>&1' EXIT
    if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$BACKUP_PASSPHRASE_FILE" -in "$file" \
            | pg_restore --no-owner --exit-on-error -d "$target"; then
        log "XƏTA: bərpa alınmadı (parol səhvdir və ya nüsxə zədələnib): $(basename "$file")"
        exit 1
    fi
    counts "$target" > /tmp/restored.txt
    counts "$PGDATABASE" > /tmp/live.txt
    psql -d postgres -XAqc "DROP DATABASE $target"
    if diff /tmp/live.txt /tmp/restored.txt > /tmp/diff.txt; then
        log "bərpa sınağı uğurludur: $(basename "$file"), $(wc -l < /tmp/live.txt) cədvəl, sətir sayları eynidir"
    else
        # nüsxədən sonra yeni yazılar ola bilər: fərq xəta deyil, amma göstərilir
        log "bərpa uğurludur, nüsxədən sonra dəyişən cədvəllər (canlı < > nüsxə):"
        cat /tmp/diff.txt
    fi
}

daemon() {
    require_passphrase
    log "işləyir: hər gün saat $BACKUP_HOUR:00, $BACKUP_KEEP_DAYS gün saxlanılır, qovluq $BACKUP_DIR"
    while true; do
        now=$(date +%s)
        next=$(date -d "today $BACKUP_HOUR:00" +%s)
        [ "$next" -le "$now" ] && next=$(date -d "tomorrow $BACKUP_HOUR:00" +%s)
        sleep $((next - now))
        once || log "nüsxə alınmadı, sabah yenidən cəhd ediləcək"
    done
}

case "${1:-}" in
    once) once ;;
    daemon) daemon ;;
    verify) shift; verify "${1:-}" ;;
    *) echo "istifadə: $0 once | daemon | verify [fayl]" >&2; exit 2 ;;
esac
