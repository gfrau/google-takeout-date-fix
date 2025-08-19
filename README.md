# Google Takeout Date Fix (v3)

Script Python per **correggere le date** di foto e video esportati da **Google Takeout**, usando i relativi file JSON (`*.json` e `*.supplemental-metadata.json`).  
Lo script copia i file in una cartella di **DESTINAZIONE** mantenendo la struttura e **riscrive i metadati** (EXIF/XMP/QuickTime).  
I file problematici vengono spostati in `DEST/errors/YYYY/MM`.

## Caratteristiche principali
- ✅ Supporto formati: **JPG, JPEG, PNG, GIF, MP4**
- 🔍 Lettura date da JSON sidecar di Google Takeout
- 🛠️ Scrittura metadati (ExifTool per immagini e video)
- ⚡ Parallelizzazione per maggiore velocità
- 🗂️ Copia non distruttiva (gli originali restano intatti)
- 🧾 Log solo per errori → più facile da analizzare
- 📁 Struttura di output identica all’originale, con cartella `errors/` per i file non corretti

## Dipendenze
- Python 3.8+
- [ExifTool](https://exiftool.org/) (obbligatorio per video e raccomandato per immagini)

## Installazione

### macOS
```bash
brew install exiftool
```

### Linux (Debian/Ubuntu)
```bash
sudo apt update
sudo apt install -y libimage-exiftool-perl
```

### Windows
```powershell
choco install exiftool
```

## Utilizzo
Quando avvii lo script, ti chiederà interattivamente la **cartella sorgente (SRC)** e la **cartella di destinazione (DEST)**.

Esempio:
```bash
python3 fix_takeout_dates_v3.py
```

## Verifica risultati
Per controllare se le date sono state corrette:
```bash
exiftool -time:all -a -G0:1 -s "/path/to/DEST/file.jpg"
```

## Log ed errori
- `errori.log`: file di log con i dettagli
- `errori_compatti.csv`: conteggio errori per tipo
- `DEST/errors/YYYY/MM/`: file che non è stato possibile correggere

## Note
- JPEG/PNG/GIF → date scritte in **ora locale**  
- MP4 → date scritte in **UTC** (per coerenza su player/piattaforme)
- iCloud/Foto potrebbe non aggiornare foto già importate: rimuovile e reimporta le versioni corrette da `DEST`.

## Licenza
MIT (puoi modificarla a piacere).
