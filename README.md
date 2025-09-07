Páciens Email Emlékeztető Rendszer v2.0

Orvosi rendelők számára készült modern email emlékeztető alkalmazás Google Calendar integrációval és automatizált időpont kezeléssel.

Főbb funkciók
- 📅 Google Calendar szinkronizáció
- 📧 Automatikus email emlékeztetők
- 👥 Páciens adatbázis kezelés
- 🌐 Többnyelvű támogatás (magyar, német)
- 🔒 Biztonságos adatkezelés (titkosított jelszavak)
- 📊 Excel import/export
- 🤖 Ütemezett automatizálás
- 📝 Sablon alapú email rendszer

Telepítés
Előfeltételek
Python 3.8 vagy újabb verzió szükséges.

Függőségek telepítése
```bash
pip install pandas cryptography schedule tkinter
pip install google-api-python-client google-auth-oauthlib
```

Google Calendar API beállítása
1. Menjen a [Google Cloud Console](https://console.cloud.google.com/)-ra
2. Hozzon létre új projektet vagy válasszon meglévőt
3. Engedélyezze a Google Calendar API-t
4. Hozzon létre OAuth 2.0 credentials-t (Desktop Application típusú)
5. Töltse le a `credentials.json` fájlt az alkalmazás mappájába

**FIGYELEM:** A `credentials.json` fájl érzékeny adatokat tartalmaz, soha ne ossza meg!

Email beállítás (Gmail esetén)
1. Engedélyezze a kétlépcsős hitelesítést Gmail fiókjában
2. Hozzon létre App Password-ot:
   - Gmail Beállítások → Biztonság → Kétlépcsős hitelesítés → Alkalmazásjelszavak
3. Használja ezt a jelszót az alkalmazásban

Használat
Indítás
```bash
python patient_reminder_app.py
```

Alapbeállítások
1. **Beállítások fül:**
   - Email konfigurálása (SMTP, jelszó)
   - Google Calendar hitelesítés
   - Teszt email küldése

2. **Páciensek fül:**
   - Új páciensek hozzáadása
   - Excel import
   - Keresés és szűrés

3. **Naptár fül:**
   - Calendar szinkronizálás
   - Események megtekintése
   - Manuális események hozzáadása

Automatizálás
Az alkalmazás automatikusan:
- **12:00-kor:** Holnapi időpontokra emlékeztetőket küld
- **15:30-kor:** Mai új időpontok visszaigazolását küldi

Excel Import formátum
| Név | Email | Telefon | Nyelv |
|-----|-------|---------|--------|
| Kovács János | kovacs@email.com | +36301234567 | hu |
| Tischler Maria | tischler@email.com | +43664123456 | de |

Biztonsági megjegyzések
- Jelszavak titkosítva tárolódnak
- GDPR kompatibilis adatkezelés
- Rendszeres backup ajánlott
- Soha ne ossza meg konfigurációs fájlokat

Hibaelhárítás
Email küldési hibák
- Ellenőrizze SMTP beállításokat
- Gmail esetén használjon App Password-ot
- Tűzfal beállítások ellenőrzése

Google Calendar hibák
- `credentials.json` fájl ellenőrzése
- Újra hitelesítés szükség esetén
- Internet kapcsolat ellenőrzése

Fájlstruktúra

```
project/
├── patient_reminder_app.py    # Fő alkalmazás
├── credentials.json           # Google API kulcsok (NE commitolja!)
├── config.json               # Email beállítások (NE commitolja!)
├── encryption.key            # Titkosítási kulcs (NE commitolja!)
├── patient_reminder.db       # SQLite adatbázis (NE commitolja!)
├── requirements.txt          # Python függőségek
└── README.md                # Ez a fájl
```

Támogatott nyelvek
- Magyar (hu)
- Német (de)

Licenc

MIT License - lásd a LICENSE fájlt részletekért.
Közreműködés
1. Fork-olja a repository-t
2. Hozzon létre feature branch-et (`git checkout -b feature/AmazingFeature`)
3. Commitolja változásait (`git commit -m 'Add some AmazingFeature'`)
4. Push-olja a branch-et (`git push origin feature/AmazingFeature`)
5. Nyisson Pull Request-et

Fejlesztő
- Email: [anitadatascience@gmail.com]
- GitHub: [DataNexusHU Anita Nemeth]
