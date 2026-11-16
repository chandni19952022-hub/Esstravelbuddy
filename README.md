# ESS Travel Buddy

Shared-ride coordination app for employees at Okhla Bird Sanctuary (OBS).

Ab yeh app **proper frontend + backend** structure mein hai, aur locally end-to-end chalti hai (Claude artifact DB ke bina).

## Folder structure

```
commute-together-app/
├── frontend/                 # UI (React via CDN + Tailwind)
│   ├── index.html            # Screens: login, nearby, chat, groups, ride
│   └── api.js                # Backend client (REST + live SSE sync)
├── backend/                  # Python Flask API
│   ├── app.py                # REST + Server-Sent Events + static UI serve
│   └── requirements.txt
├── start.bat                 # Windows: install + run (double-click)
├── start.ps1                 # PowerShell runner
└── README.md
```

### Data model
| Collection | Fields |
|---|---|
| **presence** | `empId`, `name`, `department`, `manager`, `lat`, `lon`, `checkinTime` (3h expiry) |
| **groups** | `name`, `members[]`, `memberNames{}`, `createdTime`, `status`, `pickupPoint`, `completedTime` |
| **chat** | `empId`, `name`, `text`, `time`, `groupId` (`global` or group id) |
| **feedback** | `groupId`, `empId`, `rating`, `comment`, `time` |

Group status flow: `forming` → `ready` (3+ members) → `departed` → `completed`

---

## End-to-end kaise chalayein (Windows)

### Option A — easiest (recommended)
1. Double-click `start.bat`
2. Terminal mein jo **Network URL** print ho (jaise `http://10.x.x.x:5000`) wahi phone/laptop pe kholo
3. Is PC pe: **http://127.0.0.1:5000**

### Option B — PowerShell / terminal
```powershell
cd d:\commute-together-app
.\start.ps1
```

### Option C — manual steps
```powershell
cd d:\commute-together-app\backend
py -m pip install -r requirements.txt
py app.py
```

Server band karne ke liye terminal mein `Ctrl + C`.

## Network pe chalana (phone / dusre laptops)

Server `0.0.0.0:5000` pe sunta hai — same Wi‑Fi / LAN pe sab devices access kar sakte hain.

1. `start.bat` / `start.ps1` chalao (firewall rule auto-add hone ki koshish karta hai)
2. Terminal mein dikhne wala **Network URL** note karo, e.g. `http://10.127.40.35:5000`
3. Phone/laptop ko **usi Wi‑Fi** pe rakho jisse yeh PC connected hai
4. Phone browser mein woh Network URL kholo (localhost phone pe kaam nahi karega)

Agar connect na ho:
- Windows Defender Firewall → Inbound rule: TCP **5000** Allow
- Office/guest Wi‑Fi pe client isolation on ho sakta hai — us case mein alag hotspot/LAN try karo
- Port badalne ke liye: `$env:PORT=8080; .\start.ps1`

---

## App kaise use karein (flow)

1. **Login** — Employee ID, company email, name bharo → verification screen pe jo OTP dikhe wahi enter karo (demo OTP, email nahi jata).
2. **Check in** — Location allow karke OBS pe check-in karo. Aap **Nearby** list mein dikhne lagenge.
3. **Groups** — Naya ride group banao ya kisi existing mein join karo. 3 members hone pe status `ready` ho jata hai.
4. **Chat** — Global OBS chat ya apne ride group ki chat.
5. **Ride** — Group ready hone pe ride start / complete, phir feedback.

Live sync test: do browsers (ya normal + Incognito) kholo, dono se alag employee se login karke check-in karo — dono screens pe presence/chat/groups sync hona chahiye.

---

## API (backend)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/snapshot` | Full state |
| GET | `/api/events` | Live SSE stream |
| PUT | `/api/presence/:empId` | Check-in / location update |
| DELETE | `/api/presence/:empId` | Check-out |
| POST | `/api/groups` | Create group |
| GET/PATCH/DELETE | `/api/groups/:id` | Read / update / delete group |
| POST | `/api/chat` | Send message |
| POST | `/api/feedback` | Ride feedback |

Note: data **in-memory** hai — server restart pe wipe ho jata hai (demo / local use ke liye theek).

---

## Requirements
- Python 3.10+ (`py` ya `python` PATH pe)
- Modern browser (Chrome / Edge / Firefox)
- Location permission (optional — bina location bhi check-in hota hai)
