# Google Takeout Date Fix

Script Python per **correggere le date** di foto e video esportati da **Google Takeout**, usando i relativi JSON (inclusi i `*.supplemental-metadata.json` e i `metadata.json/metadati.json` a livello album).  
Lo script copia i file in una cartella di **DESTINAZIONE** mantenendo la struttura e **riscrive i metadati** (EXIF/XMP/QuickTime). I file problematici vengono spostati in `DEST/errors/YYYY/MM`.

## Caratteristiche principali
- ✅ Supporto formati: **JPG, JPEG, HEIC, PNG, GIF, MP4**
- 🔍 Lettura date da JSON sidecar, metadata d’album o fallback da metadati interni
- 🛠️ Scrittura EXIF/XMP/QuickTime e timestamp file
- 🧪 Auto-fix su JPEG corrotti e rinomina estensione errata
- ⚡ Parallelizzato e ricorsivo
- 🗂️ Copia non distruttiva (gli originali restano intatti)
- 🧾 Log errori dettagliati e compatti

## Dipendenze
- Python 3.8+
- [ExifTool](https://exiftool.org/) (obbligatorio per video, consigliato per immagini)
- [piexif](https://pypi.org/project/piexif/) (opzionale, fallback su JPEG)

## Installazione

### macOS
```bash
brew install exiftool
pip3 install piexif
```

### Linux (Debian/Ubuntu)
```bash
sudo apt update
sudo apt install -y libimage-exiftool-perl
python3 -m pip install --user piexif
```

### Windows
```powershell
choco install exiftool
py -m pip install piexif
```

## Utilizzo
```bash
python fix_takeout_dates_v4.py SRC DEST
```
- `SRC`: cartella Takeout di origine  
- `DEST`: cartella di destinazione (copie corrette)

Esempio:
```bash
python3 fix_takeout_dates_v4.py ~/Downloads/Takeout ~/Pictures/TakeoutFixed
```

## Verifica risultati
```bash
exiftool -time:all -a -G0:1 -s "/path/to/DEST/file.jpg"
```

Report CSV globale:
```bash
exiftool -r -csv -DateTimeOriginal -CreateDate -ModifyDate \
 -XMP:DateCreated -FileCreateDate -FileModifyDate \
 -MediaCreateDate -TrackCreateDate \
 "/path/to/DEST" > "/path/to/DEST/audit_date_report.csv"
```

## Log ed errori
- `errori.log`: dettagli per file
- `errori_compatti.csv`: conteggi per tipo di errore
- `DEST/errors/YYYY/MM/`: file che non è stato possibile correggere

## Note
- JPEG → date in **ora locale**  
- MP4 → date in **UTC** (per coerenza su player/piattaforme)
- iCloud/Foto potrebbe non aggiornare foto già importate: rimuovile e reimporta le versioni corrette da `DEST`.

## Licenza
MIT (puoi modificarla a piacere).
