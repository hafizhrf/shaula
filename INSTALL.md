# Panduan Instalasi untuk Pemula — Shaula (Discord × Claude Code)

Panduan ini menuntun kamu **dari nol** untuk memasang bot ini di **VPS** *atau* **laptop/PC
lokal**, menghubungkannya ke Discord, dan di akhir bisa **nge-prompt Figma** lewat Claude
(karena kita pasang **Figma MCP** di agent Claude-nya).

Bot ini bekerja seperti ini:

```
Kamu ketik /task di Discord  ──▶  Bot (Python)  ──▶  menjalankan  claude  (Claude Code CLI)
                                                       └▶ claude yang benar-benar kerja:
                                                          baca/tulis file, jalankan shell,
                                                          panggil MCP (mis. Figma), dsb.
```

Jadi ada **3 bahan utama** yang harus jalan: **Python + bot**, **Claude Code CLI**, dan
**bot Discord**. Figma adalah bonus di akhir (lewat MCP).

> Kalau kamu ingin referensi arsitektur lengkap, lihat [`AGENTS.md`](AGENTS.md). Aturan main
> runtime untuk agent ada di [`CLAUDE.md`](CLAUDE.md). File ini fokus ke **pemasangan**.

---

## Daftar Isi

1. [Yang kamu butuhkan](#1-yang-kamu-butuhkan)
2. [Pilih tempat: VPS atau laptop lokal](#2-pilih-tempat-vps-atau-laptop-lokal)
3. [Langkah 1 — Install Python, Node.js, Git](#langkah-1--install-python-nodejs-git)
4. [Langkah 2 — Install & login Claude Code CLI](#langkah-2--install--login-claude-code-cli)
5. [Langkah 3 — Buat bot Discord](#langkah-3--buat-bot-discord)
6. [Langkah 4 — Ambil kode bot & pasang dependensi](#langkah-4--ambil-kode-bot--pasang-dependensi)
7. [Langkah 5 — Isi file `.env`](#langkah-5--isi-file-env)
8. [Langkah 6 — Jalankan & tes di Discord](#langkah-6--jalankan--tes-di-discord)
9. [Langkah 7 — Jalankan otomatis (systemd, khusus VPS)](#langkah-7--jalankan-otomatis-systemd-khusus-vps)
10. [Langkah 8 — Pasang Figma MCP supaya bisa nge-prompt Figma](#langkah-8--pasang-figma-mcp-supaya-bisa-nge-prompt-figma)
11. [Troubleshooting (masalah umum)](#troubleshooting-masalah-umum)

---

## 1. Yang kamu butuhkan

Sebelum mulai, siapkan:

- **Sebuah komputer Linux** — VPS Ubuntu 24.04 **atau** laptop (Ubuntu/Debian; macOS &
  Windows/WSL juga bisa dengan sedikit penyesuaian).
- **Akun Discord** + sebuah **server Discord** milikmu (tempat bot akan bekerja).
- **Akun Claude** — salah satu dari:
  - Langganan **Claude Pro/Max** (login lewat browser, tanpa bayar per-token), **atau**
  - **API key** Anthropic (`sk-ant-...`) — bayar sesuai pemakaian.
- **Koneksi internet** dan akses **terminal** (di VPS lewat SSH; di laptop buka aplikasi
  Terminal).
- (Opsional) **Akun Figma** untuk bagian Figma di Langkah 8.

> **Belum pernah pakai terminal?** Terminal adalah kotak hitam tempat kamu mengetik perintah.
> Di VPS kamu masuk lewat SSH: `ssh namauser@alamat-ip-vps`. Di laptop, cari aplikasi
> bernama **Terminal**.

---

## 2. Pilih tempat: VPS atau laptop lokal

| | **VPS** (server sewaan) | **Laptop / PC lokal** |
|---|---|---|
| Bot hidup 24 jam? | ✅ Ya, selama VPS nyala | ❌ Hanya saat laptop nyala & bot dijalankan |
| Cocok untuk | Produksi, dipakai tim | Belajar, coba-coba, dev |
| Figma **Dev Mode** lokal (app Figma) | ❌ (VPS tak punya layar/app Figma) | ✅ Bisa (Langkah 8 opsi B) |
| Figma **remote** (`mcp.figma.com`) | ✅ Bisa | ✅ Bisa |

Langkah 1–6 **sama persis** untuk keduanya. Langkah 7 (systemd auto-start) khusus VPS.
Langkah 8 (Figma) punya dua opsi tergantung tempat.

---

## Langkah 1 — Install Python, Node.js, Git

Bot ditulis dengan **Python 3.12+**. Claude Code CLI butuh **Node.js 18+**. Git untuk
mengambil kode.

**Di Ubuntu/Debian (VPS maupun laptop):**

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl

# Node.js 20 LTS (untuk Claude Code CLI)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

**Cek versinya** (semua harus keluar angka, bukan "command not found"):

```bash
python3 --version   # harus 3.12 atau lebih
node --version      # harus v18 atau lebih (mis. v20.x)
git --version
```

> **macOS:** pakai [Homebrew](https://brew.sh): `brew install python git node`.
> **Windows:** pakai **WSL2** (Ubuntu) lalu ikuti perintah Ubuntu di atas — jauh lebih mulus.

---

## Langkah 2 — Install & login Claude Code CLI

Ini "otak" yang mengerjakan tugas. Install lewat npm:

```bash
sudo npm install -g @anthropic-ai/claude-code
```

Cek berhasil:

```bash
claude --version        # mis. 2.1.181 (Claude Code)
which claude            # mis. /usr/bin/claude  ← catat path ini untuk .env
```

> ⚠️ Kalau `which claude` menghasilkan path **selain** `/usr/bin/claude` (mis.
> `/usr/local/bin/claude` atau `~/.npm-global/bin/claude`), catat — nanti diisi ke
> `CLAUDE_BIN` di `.env`.

**Login Claude** — pilih salah satu:

**Opsi A — Langganan Pro/Max (disarankan, tanpa bayar per-token):**

```bash
claude login
```

Ikuti instruksi: akan muncul URL, buka di browser, login, tempel kode kembali ke terminal.

> **Di VPS tanpa browser?** Buka URL yang muncul itu di browser **laptopmu**, login, lalu
> salin kode hasilnya kembali ke terminal VPS. Login ini tersimpan di `~/.claude` dan
> dipakai ulang oleh bot secara otomatis.

**Opsi B — API key:** lewati `claude login`; nanti cukup isi `ANTHROPIC_API_KEY=sk-ant-...`
di `.env` (Langkah 5).

**Tes cepat** bahwa Claude jalan:

```bash
claude --print "halo, balas satu kata"
```

Kalau membalas, berarti Claude siap. ✅

---

## Langkah 3 — Buat bot Discord

Bot ini punya identitas utama bernama **Shaula** (eksekutor). Kita buat **satu aplikasi bot
Discord** untuknya.

1. Buka **https://discord.com/developers/applications** → **New Application** → beri nama
   (mis. `Shaula`) → **Create**.
2. Menu kiri **Bot** → **Add Bot** (Reset Token bila perlu) → **Copy** tokennya.
   > 🔒 Token ini rahasia, seperti password. Jangan dibagikan / commit ke Git. Kita simpan
   > sebagai `SHAULA_DISCORD_TOKEN`.
3. Masih di **Bot**, scroll ke **Privileged Gateway Intents** → **aktifkan
   `MESSAGE CONTENT INTENT`** → Save.
4. Menu kiri **OAuth2 → URL Generator**:
   - **Scopes:** centang `bot` dan `applications.commands`.
   - **Bot Permissions:** minimal `Send Messages`, `Read Message History`,
     `Create Public Threads`, `Send Messages in Threads`, `Manage Threads`,
     `Embed Links`, `Attach Files`.
   - Salin URL di bawah, buka di browser, pilih server-mu, **Authorize**. Bot kini masuk
     ke server.
5. **Ambil Server ID (Guild ID):** di Discord aktifkan **Developer Mode**
   (User Settings → Advanced → Developer Mode). Lalu klik-kanan nama server → **Copy
   Server ID**. Simpan sebagai `DISCORD_GUILD_ID`.

> **Butuh Emilia (persona front-end)?** Untuk pemula, **tidak perlu**. Kita jalankan mode
> **Shaula-only** (`EMILIA_ENABLED=false`). Emilia adalah layanan terpisah dan opsional —
> lihat [`AGENTS.md`](AGENTS.md) bila nanti ingin mengaktifkannya.

---

## Langkah 4 — Ambil kode bot & pasang dependensi

Kita taruh proyek di folder kerja. Di VPS ini standarnya `/home/ubuntu/workspace`.

```bash
# masuk ke folder kerja (buat kalau belum ada)
mkdir -p ~/workspace && cd ~/workspace

# ambil kodenya (ganti URL bila repomu berbeda)
git clone https://github.com/hafizhrf/shaula.git discord-devops-bot
cd discord-devops-bot
```

> Kalau kamu **sudah** punya folder ini (mis. `/home/ubuntu/workspace/discord-devops-bot`),
> cukup `cd` ke sana dan lewati `git clone`.

Buat **virtualenv** (lingkungan Python terpisah) lalu pasang dependensinya:

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

Dependensi yang terpasang: `discord.py`, `psutil`, `docker`, `python-dotenv`.

---

## Langkah 5 — Isi file `.env`

`.env` adalah tempat menaruh token & pengaturan. Salin dari contoh:

```bash
cp .env.example .env
nano .env        # atau: vim .env / editor favoritmu
```

Untuk **pemula (mode Shaula-only)**, cukup isi/pastikan baris-baris ini:

```ini
# --- Discord ---
# DISCORD_TOKEN wajib ADA (dibaca config), tapi di mode Shaula-only tidak dipakai
# untuk konek. Boleh diisi token apa saja yang valid formatnya, atau token bot lain.
DISCORD_TOKEN=isi_token_apa_saja_yang_penting_terisi
DISCORD_GUILD_ID=1234567890        # Server ID dari Langkah 3

# Jangan konekkan Emilia bawaan (kita hanya pakai Shaula)
EMILIA_ENABLED=false

# --- Bot Shaula (yang benar-benar dipakai) ---
SHAULA_DISCORD_TOKEN=token_bot_Shaula_dari_Langkah_3

# --- Claude ---
# Kosongkan kalau sudah `claude login` (Opsi A). Isi kalau pakai API key (Opsi B).
ANTHROPIC_API_KEY=
CLAUDE_BIN=/usr/bin/claude          # sesuaikan dengan hasil `which claude`
CLAUDE_MODEL=                       # kosong = default akun; isi "sonnet" untuk lebih hemat

# --- Lokasi kerja task ---
PROJECTS_BASE_DIR=/home/ubuntu/workspace/projects
```

> **Kenapa `DISCORD_TOKEN` tetap wajib diisi?** `config.py` menandainya *required* meski di
> mode Shaula-only ia tak dipakai untuk konek. Isi apa saja asalkan tidak kosong (idealnya
> token bot Discord kedua bila punya). Yang benar-benar konek adalah `SHAULA_DISCORD_TOKEN`.

Pastikan folder projects ada dan bisa ditulis:

```bash
mkdir -p /home/ubuntu/workspace/projects
```

Simpan file (`nano`: `Ctrl+O`, `Enter`, lalu `Ctrl+X`).

> 🔒 `.env` berisi rahasia dan sudah masuk `.gitignore` — **jangan** commit. Daftar
> pengaturan lengkap ada di `config.py` (sumber kebenaran) dan `.env.example` (template
> beranotasi).

---

## Langkah 6 — Jalankan & tes di Discord

Jalankan bot langsung (mode uji, tetap terlihat lognya):

```bash
cd ~/workspace/discord-devops-bot
./venv/bin/python bot.py
```

Di log kamu harus melihat konfirmasi seperti:

```
... Emilia client DISABLED ... Running Shaula-only in this process
... slash commands synced ...
```

**Tes di Discord:**

1. Di server-mu, ketik `/task` — harusnya muncul perintah slash bot.
2. Coba: `/task cek pemakaian disk server dan ringkas`.
3. Bot akan membuka **thread**, menjalankan Claude, dan menstreaming hasilnya ke Discord.

Perintah slash yang tersedia (mode Shaula-only):

| Perintah | Fungsi |
|---|---|
| `/task <deskripsi>` | Jalankan tugas full-auto oleh Claude di sebuah thread |
| `/task-kantor <deskripsi>` | Sama, pakai akun Claude cadangan (kalau kena limit) |
| `/task-file <path> [kantor]` | Baca file rencana lalu jalankan sebagai task |
| `/deploy <target>` | Task dengan **persetujuan tombol** dulu sebelum eksekusi |
| `/runs [limit]` · `/run <id>` | Riwayat run yang tersimpan |
| `/stop-task [id]` | Hentikan task berjalan & tutup sesinya |
| `/server-health` | CPU / RAM / disk / uptime |
| `/docker-status` · `/docker-logs <nama>` | Status/log Docker (butuh Docker) |
| `/readfile <path>` | Tampilkan isi file ke Discord |

Kalau semua jalan, hentikan dulu dengan `Ctrl+C` — lanjut ke Langkah 7 supaya bot hidup
otomatis.

---

## Langkah 7 — Jalankan otomatis (systemd, khusus VPS)

Supaya bot **hidup 24 jam** dan **otomatis nyala lagi** kalau VPS reboot, daftarkan sebagai
service systemd. Sudah disediakan file `deploy/discord-devops-bot.service`.

```bash
sudo cp deploy/discord-devops-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now discord-devops-bot
```

Mengelola service:

```bash
systemctl is-active discord-devops-bot                     # cek hidup/mati
journalctl -u discord-devops-bot -n 50 --no-pager          # lihat 50 baris log terakhir
journalctl -u discord-devops-bot -f                        # ikuti log real-time
sudo systemctl restart discord-devops-bot                  # restart setelah ubah kode/.env
```

> ⚠️ **PENTING:** file service default berjalan dari
> `/home/ubuntu/workspace/discord-devops-bot` sebagai user `ubuntu`. Kalau path/user-mu
> beda, edit file `.service` itu (baris `WorkingDirectory`, `EnvironmentFile`,
> `ExecStart`, `User`) sebelum `daemon-reload`.
>
> ⚠️ **Jangan pernah** menyuruh bot me-restart service-nya sendiri dari dalam sebuah task —
> ia berjalan **sebagai** service itu, jadi restart akan membunuh task-nya sendiri. Lihat
> `CLAUDE.md`.

**Di laptop lokal** kamu tidak wajib systemd — cukup jalankan `./venv/bin/python bot.py` di
terminal saat mau memakai (biarkan terminal terbuka). Kalau mau, bisa pakai `tmux`/`screen`
agar tetap jalan setelah terminal ditutup.

---

## Langkah 8 — Pasang Figma MCP supaya bisa nge-prompt Figma

**MCP** (Model Context Protocol) adalah cara Claude terhubung ke alat luar. Dengan **Figma
MCP**, Claude bisa membaca desain Figma-mu, sehingga kamu bisa nge-prompt seperti:

> `/task ambil frame "Login" dari file Figma ini <url> lalu buatkan komponen React-nya`

### Konsep penting (baca dulu!)

Bot menjalankan `claude` sebagai **subprocess** yang mewarisi konfigurasi **user** di
`~/.claude`. Karena itu MCP **harus** didaftarkan di **scope `user`** (`-s user`), bukan
`local`/`project` — kalau tidak, task yang berjalan di folder session tidak akan melihatnya.

Ada **dua cara** memasang Figma MCP. Pilih sesuai tempatmu:

---

### Opsi A — Figma **remote** MCP (bisa di VPS **dan** laptop) — disarankan

Server resmi Figma yang berjalan di cloud (`https://mcp.figma.com/mcp`). Cocok untuk VPS
karena tidak butuh aplikasi Figma terpasang di mesin.

**1. Daftarkan servernya (scope user):**

```bash
claude mcp add --transport http -s user figma https://mcp.figma.com/mcp
```

**2. Autentikasi sekali secara interaktif.** Server ini pakai OAuth, jadi harus login sekali
lewat sesi `claude` interaktif (bot yang headless **tidak bisa** melakukan login ini
sendiri):

```bash
claude          # buka sesi interaktif
```

Di dalam sesi, ketik:

```
/mcp
```

Pilih **figma** → **Authenticate/Login** → ikuti URL, login Figma, setujui. Setelah status
jadi **connected/authenticated**, keluar (`/exit`).

> Kredensial OAuth tersimpan di `~/.claude`, sehingga **task bot ikut memakainya** tanpa
> perlu login ulang. Di VPS tanpa browser: buka URL yang muncul di browser laptopmu, login,
> lalu tempel kembali kodenya ke terminal VPS.

**3. Verifikasi:**

```bash
claude mcp list      # 'figma' harus muncul & 'connected' / tercentang
```

---

### Opsi B — Figma **Dev Mode** MCP lokal (hanya laptop dengan app Figma)

Aplikasi **Figma Desktop** punya server MCP lokal di `http://127.0.0.1:3845/mcp`. Ini hanya
bekerja di **laptop yang menjalankan aplikasi Figma** (tidak berlaku di VPS headless).

**1. Nyalakan di Figma Desktop:** buka Figma → menu **Figma → Preferences** →
aktifkan **"Enable local MCP Server"** (atau **Dev Mode MCP Server**). Server akan hidup di
port `3845`.

**2. Daftarkan ke Claude (scope user):**

```bash
claude mcp add --transport http -s user figma-dev http://127.0.0.1:3845/mcp
```

**3. Verifikasi:**

```bash
claude mcp list      # 'figma-dev' harus 'connected'
```

Karena lokal, biasanya tidak perlu OAuth — cukup app Figma menyala dengan file/frame
terbuka.

---

### Coba nge-prompt Figma dari Discord

Setelah salah satu opsi di atas **connected**, restart bot bila sedang jalan sebagai service
(agar subprocess baru membaca config MCP terbaru):

```bash
sudo systemctl restart discord-devops-bot     # kalau pakai systemd
```

Lalu di Discord:

```
/task Buka desain Figma di <link-atau-frame>, jelaskan struktur layer-nya,
      lalu buatkan kerangka HTML/CSS-nya di /home/ubuntu/workspace/projects/figma-test
```

Claude akan memanggil tool Figma MCP untuk membaca desain dan mengerjakan permintaanmu.

> **Catatan:** fitur Dev Mode MCP Figma umumnya butuh akun Figma dengan akses Dev Mode
> (seat berbayar). Kalau tool Figma tidak muncul, cek `claude mcp list` dan pastikan
> statusnya `connected`/`authenticated`, bukan `needs authentication`.

---

## Troubleshooting (masalah umum)

**Bot tidak muncul online / slash command tak ada**
- Cek log: `journalctl -u discord-devops-bot -n 80 --no-pager` (atau lihat output
  `python bot.py`).
- Pastikan `SHAULA_DISCORD_TOKEN` benar dan **Message Content Intent** aktif (Langkah 3.3).
- Slash command disinkron ke `DISCORD_GUILD_ID` saat start — pastikan Server ID benar dan
  bot sudah diundang ke server itu. Coba tunggu ~1 menit atau restart bot.

**`Missing required env var: DISCORD_TOKEN`**
- `DISCORD_TOKEN` di `.env` kosong. Isi (boleh token bot kedua) — lihat Langkah 5.

**`claude: command not found` di log bot**
- Path Claude bukan `/usr/bin/claude`. Jalankan `which claude`, lalu set `CLAUDE_BIN` di
  `.env` ke path itu, dan restart bot.

**Task gagal dengan error otentikasi / rate limit Claude**
- Jalankan `claude --print "tes"` sebagai user yang sama dengan bot (`ubuntu`) untuk
  memastikan login masih valid. Ulang `claude login` bila perlu.
- Kena limit 5-jam? Coba `/task-kantor` (akun cadangan) atau tunggu reset.

**Figma tool tidak terpanggil**
- `claude mcp list` harus menampilkan server Figma **connected**, bukan
  `needs authentication`. Jika perlu, ulang Opsi A langkah 2 (`claude` → `/mcp` →
  authenticate).
- Pastikan MCP didaftarkan dengan **`-s user`**. Cek: server harus muncul saat menjalankan
  `claude mcp list` dari home directory user `ubuntu`.
- Restart service bot setelah menamb/meng-auth MCP agar subprocess membaca ulang config.

**Perubahan kode/.env tidak berpengaruh**
- Bot memuat konfigurasi saat start. Setelah mengedit `.env` atau kode, **restart**:
  `sudo systemctl restart discord-devops-bot`.

---

## Ringkasan perintah (cheat sheet)

```bash
# Install prasyarat
sudo apt install -y python3 python3-venv git curl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs
sudo npm install -g @anthropic-ai/claude-code
claude login

# Pasang bot
cd ~/workspace/discord-devops-bot
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env          # isi token & guild id

# Jalan sebagai service
sudo cp deploy/discord-devops-bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now discord-devops-bot
journalctl -u discord-devops-bot -f

# Figma MCP (scope user!)
claude mcp add --transport http -s user figma https://mcp.figma.com/mcp
claude            # lalu ketik /mcp untuk autentikasi Figma
claude mcp list   # pastikan 'connected'
```

Selamat! Botmu siap dipakai dan bisa nge-prompt Figma. 🎉 Untuk detail arsitektur lanjut,
baca [`AGENTS.md`](AGENTS.md) dan [`README.md`](README.md).
