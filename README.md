# CakBro – Safe Exam Browser (Python / PySide6)

Port dari `CakBro.ahk` v2.10.2. Satu kode untuk **Windows, Linux, macOS**; dikompilasi
otomatis oleh GitHub Actions (PyInstaller).

## Menjalankan (pengembangan)
```bash
pip install -r requirements.txt
python cakbro.py --dev      # jendela biasa, tanpa kunci OS
python cakbro.py            # mode kiosk penuh (hati-hati: fullscreen, always-on-top)
```
Keluar: **Ctrl+Alt+Shift+Q** (di macOS: Control+Option+Shift+Q).

## Build di GitHub
1. Push folder ini ke repo GitHub.
2. Tab **Actions → Build CakBro → Run workflow** (artefak ada di halaman run), atau
   buat tag `git tag v2.10.2 && git push --tags` → rilis otomatis berisi:
   `CakBro.exe` (Windows), `CakBro-linux`, `CakBro-macos.zip`, dan `SHA256SUMS.txt`.

## Auto-update
Sama seperti versi AHK: saat start, `latest.ini` dibaca lalu file rilis diunduh dan
diverifikasi SHA-256 sebelum dipasang. Format `latest.ini`:
```ini
version=2.10.3
sha256=<hash CakBro.exe>
sha256_linux=<hash CakBro-linux>
```
Isi hash dari `SHA256SUMS.txt`. Auto-install hanya Windows & Linux; macOS tidak
(unduh manual). Ubah `UPDATE_MANIFEST_URL` / `UPDATE_BASE_URL` di `cakbro.py` bila repo rilis berbeda.

## Perbedaan dari versi AHK
| AHK | Python |
|---|---|
| Mengendalikan Edge/Chrome/Brave/Firefox | Browser Chromium tertanam (QtWebEngine) – tidak perlu browser terpasang |
| Baca judul semua jendela (`|CAKBRO_AHK|`) | Baca judul halaman langsung – protokol HTML **tidak berubah** |
| Profil di folder skrip | Profil di folder data pengguna (`%LOCALAPPDATA%\CakBro`, `~/Library/Application Support/CakBro`, `~/.local/share/CakBro`) |
| `taskkill` semua browser saat start | Dihapus (tidak perlu) |
| Blokir tombol via Hotkey AHK | `QShortcut` + hook keyboard Windows + presentation options macOS |

## Batasan
* **Windows**: Win, Alt+Tab, Alt+F4, Ctrl+Esc, PrintScreen diblokir lewat hook; taskbar disembunyikan. Ctrl+Alt+Del tidak bisa diblokir aplikasi mana pun.
* **macOS**: Dock/menu bar/Cmd+Tab/Force Quit dinonaktifkan via presentation options. Build tidak ditandatangani – pada Mac siswa mungkin perlu klik kanan → Open pertama kali.
* **Linux**: tidak ada penguncian tombol tingkat OS (terutama Wayland). Disarankan menjalankan dalam sesi kiosk/akun khusus ujian.
* Penutup aplikasi terlarang (terminal, kalkulator, dsb.) aktif otomatis pada build hasil kompilasi, tidak pada `--dev`.
* Login Google di dalam browser tertanam bisa ditolak Google pada beberapa akun; User-Agent sudah disamarkan, tetapi **uji dengan alur login ujian Anda**.
* Build dengan `--onefile` butuh beberapa detik untuk ekstraksi saat start.
