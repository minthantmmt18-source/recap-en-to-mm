# Put Recap → Myanmar on your own server

The Windows app is unchanged. This adds a website for the same tool, so you can use it from a
phone or a computer browser. One password protects the whole site. There are no accounts.

The server this was written for: a Hostinger KVM VPS, Ubuntu, 2 CPU cores, 8 GB RAM, no graphics card.
Whisper (the part that listens to the video) runs on the CPU. English uses the `small.en` model.
Other languages use `small` by default, which fits in 8 GB. You can pick `medium` in the website
if you want it more accurate and slower.

---

## English

### What you need before you start

1. The VPS is on, and you can log in with SSH (Hostinger’s browser terminal works too).
2. The VPS public IP address. In Hostinger: VPS → your server → Overview → IP address. It looks like `123.45.67.89`.
3. A domain name you own, for example `recap.example.com`.
4. A Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
5. A password you will type to open the website. Make it long. Do not use your Gemini key as the password.

### 1. Point the domain at the VPS

At the place you bought the domain (or in Hostinger → DNS):

1. Open the DNS records for the domain.
2. Add or edit an **A record**:
   - Name / host: `@` if you want `example.com`, or `recap` if you want `recap.example.com`
   - Value / points to: the VPS IP
   - TTL: leave the default
3. Save.

Wait 5–30 minutes. Check from your computer:

```bash
ping recap.example.com
```

The IP it prints should be the VPS IP. HTTPS in the next steps will fail until this matches.

### 2. Copy the program onto the VPS

SSH into the server (`ssh root@YOUR_IP` or the user Hostinger gave you). Then:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/YOUR_NAME/YOUR_REPO.git
cd YOUR_REPO
```

Use the address from your GitHub page: **Code → HTTPS**. If you downloaded a ZIP instead, unzip it and `cd` into that folder.

### 3. Run the installer

One command does the rest: Python 3.12, FFmpeg, Playwright Chromium (for Myanmar text on the video),
Noto Sans Myanmar, a 2 GB swap file if the server has none, a `systemd` service, Nginx, and a free
HTTPS certificate.

Replace the four values. Keep the quotes.

```bash
sudo DOMAIN=recap.example.com \
  WEB_PASSWORD='a-long-password-you-choose' \
  GEMINI_API_KEY='paste-your-key' \
  CERTBOT_EMAIL='you@example.com' \
  bash deploy/install.sh
```

What you should see at the end: `The web app is running.` and a line of JSON like `{"ok":true,...}`.

The installer is safe to run again. It does **not** delete your `.env`, uploaded videos, or finished jobs.

If you leave `WEB_PASSWORD` out, the script invents one and prints it once. Save it.

The website files live in `/opt/recap-myanmar`. The service name is `recap-web`.
The Python app listens only on `127.0.0.1:8765`. Nginx is what the internet talks to.

### 4. Open it

On your phone or computer, open:

```text
https://recap.example.com
```

Type the password. Then:

1. Choose a video (or drop it on the page). Large files are allowed (Nginx accepts up to 8 GB).
2. Pick the source language, the Myanmar voice, speed, ratio, and quality. **720p** is a good default on 2 cores. **480p** is the fastest.
3. Press **Recap စမယ် / ဆက်မယ်**. The job keeps running on the server if you lock the phone. Come back to the same page to see progress and download.
4. When the translation step finishes, **Edit narration** opens. Fix a Myanmar line, Save, then press Start / Resume. Only the lines you changed get a new voice.
5. Download the mp4 (and the srt, transcript, and plan) from the status box.
6. The **Splitter** tab cuts one video into parts. **Jobs** lists older runs.

Only one job runs at a time. Others wait in a queue of up to 8.

The first video in a language downloads a Whisper model (about 500 MB for English or `small`, about 1.5 GB if you pick `medium`). The log shows the download. It is kept for next time.

### 5. Settings that stay in `.env`

```bash
sudo nano /opt/recap-myanmar/.env
sudo systemctl restart recap-web
```

| Name | Meaning |
|---|---|
| `WEB_PASSWORD` | The login password |
| `GEMINI_API_KEY` | Translation (and Gemini voices, if you pick one) |
| `GEMINI_IMAGE_PROJECT_1_KEY` | Extra keys, used when one runs out. Same as the desktop app |
| `GEMINI_PROXY` | Only if Gemini refuses your server’s country. Example: `http://127.0.0.1:10808`. `none` means always direct |
| `EDGE_TTS_PROXY` | Usually leave empty. Microsoft often blocks VPN servers |
| `HF_ENDPOINT` | Only if huggingface.co does not open. Example: `https://hf-mirror.com` |
| `WEB_COOKIE_SECURE` | The installer sets this to `1` after HTTPS works. Do not turn it on while you are still using plain `http://` or the password will not stick |

Do not commit `.env` or send it to anyone. It holds your key and password.

To change the non-English Whisper model without editing files, use the **Whisper (English မဟုတ်ရင်)** menu on the site. `small` is the default for this server. `medium` needs more memory.

### 6. Update later

On your computer you do not need to do this. On the VPS, in the folder you cloned:

```bash
cd ~/YOUR_REPO
git pull
sudo bash deploy/install.sh
```

You do not pass the password again if it is already in `/opt/recap-myanmar/.env`.

### 7. If something goes wrong

| What you see | What to do |
|---|---|
| The site does not open | `ping` the domain and check it is the VPS IP. On the server: `sudo systemctl status recap-web nginx` |
| `502 Bad Gateway` | The Python service is stopped. `sudo journalctl -u recap-web -n 80 --no-pager` then `sudo systemctl restart recap-web` |
| Login returns to the password page | You are on `http://` after HTTPS was turned on. Use `https://`. Or the password is wrong |
| `Gemini API key` in the log | Put the key in `/opt/recap-myanmar/.env` and `sudo systemctl restart recap-web` |
| Job sits on “downloading the Whisper model” | First run only. Wait. The log updates every few seconds. If huggingface.co is blocked, set `HF_ENDPOINT` |
| Edge voice fails with 403 | Turn off any VPN on the **server**. Microsoft blocks many VPN addresses |
| Gemini says the country is not supported | Set `GEMINI_PROXY` to a proxy in a country Gemini allows, then restart |
| Myanmar text on the video is boxes or blank | `sudo apt install fonts-noto-core` and `sudo systemctl restart recap-web`. Title text needs Playwright Chromium, which the installer sets up |
| Upload fails on a big file | Nginx must be the site from this installer (`client_max_body_size 8G`). `sudo nginx -t` |
| Disk fills up | Old jobs are under `/opt/recap-myanmar/jobs` and uploads under `web_data/uploads`. Delete a job in the Jobs tab and choose to delete files |
| See the live log | `sudo journalctl -u recap-web -f` |

Useful commands:

```bash
sudo systemctl restart recap-web
sudo systemctl status recap-web
curl -s http://127.0.0.1:8765/healthz
```

`healthz` only answers on the server itself. The public address is your domain.

### What the website does not copy from the desktop window

- Dragging a blur box is basic (add a box, then drag it). There is no corner-resize handle.
- There is no folder batch. Upload the next video when you are ready; the queue runs one at a time.
- The Gemini `.env` file is always the one on the server. There is no “browse for another .env”.
- A stopped job does not continue by itself after a reboot. Open the site and press Start / Resume. Finished steps are kept.

---

## မြန်မာ

Windows app ကို မဖျက်ပါ။ အဲ့ဒီ tool ကိုပဲ browser (ဖုန်း အပါအဝင်) ကနေ သုံးလို့ရအောင် website လုပ်ထားတာပါ။
စကားဝှက် **တစ်ခု** နဲ့ site တစ်ခုလုံးကို ကာကွယ်ထားပါတယ်။ အကောင့် အများကြီး ခွဲမထားပါ။

ဒီလမ်းညွှန်က Hostinger KVM VPS (Ubuntu, CPU ၂ လုံး, RAM 8 GB, GPU မရှိ) အတွက် ရေးထားပါတယ်။
ဗီဒီယိုထဲက စကားကို နားထောင်တဲ့ Whisper က CPU နဲ့ လုပ်ပါတယ်။ English ဆို `small.en`။
တခြားဘာသာက ပုံမှန် `small` (8 GB နဲ့ ကိုက်ပါတယ်)။ ပိုတိကျချင်ရင် website မှာ `medium` ပြောင်းရွေးလို့ရပါတယ်။ ပိုကြာပါတယ်။

### မစခင် လိုအပ်တာ

1. VPS ဖွင့်ထားပြီး SSH နဲ့ ဝင်လို့ရတယ် (Hostinger ရဲ့ browser terminal လည်း ရပါတယ်)။
2. VPS ရဲ့ public IP။ Hostinger မှာ VPS → server → Overview → IP address။ ပုံစံ `123.45.67.89`။
3. ကိုယ့် domain။ ဥပမာ `recap.example.com`။
4. Gemini API key — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) ကနေ အခမဲ့ ယူပါ။
5. Website ဖွင့်ဖို့ စကားဝှက်တစ်ခု။ ရှည်ရှည် ရွေးပါ။ Gemini key ကို စကားဝှက်အဖြစ် မသုံးပါနဲ့။

### ၁။ Domain ကို VPS ဆီ ညွှန်ပါ

Domain ဝယ်ထားတဲ့နေရာ (သို့) Hostinger → DNS မှာ —

1. အဲ့ domain ရဲ့ DNS records ကို ဖွင့်ပါ။
2. **A record** တစ်ခု ထည့်ပါ (ရှိပြီးသားဆို ပြင်ပါ)။
   - Name / host: `example.com` ကို သုံးမယ်ဆို `@`။ `recap.example.com` ဆို `recap`
   - Value: VPS IP
   - TTL: ပုံမှန်အတိုင်း ထားပါ
3. Save လုပ်ပါ။

၅ မိနစ်ကနေ မိနစ် ၃၀ လောက် စောင့်ပါ။ ကိုယ့်ကွန်ပျူတာမှာ —

```bash
ping recap.example.com
```

ပေါ်လာတဲ့ IP က VPS IP နဲ့ တူရပါမယ်။ မတူသေးရင် HTTPS အဆင့်က မအောင်မြင်ပါဘူး။

### ၂။ ပရိုဂရမ်ကို VPS ပေါ် ကူးပါ

Server ထဲ SSH ဝင်ပါ (`ssh root@YOUR_IP` သို့မဟုတ် Hostinger ပေးတဲ့ user)။ ပြီးရင် —

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/YOUR_NAME/YOUR_REPO.git
cd YOUR_REPO
```

GitHub စာမျက်နှာက **Code → HTTPS** လိပ်စာကို ထည့်ပါ။ ZIP ဒေါင်းလုဒ်လုပ်ထားရင် ဖြည်ပြီး အဲ့ folder ထဲ `cd` ဝင်ပါ။

### ၃။ Installer ကို run ပါ

အောက်က command တစ်ကြောင်းက Python 3.12, FFmpeg, Playwright Chromium (ဗီဒီယိုပေါ်က မြန်မာစာအတွက်),
Noto Sans Myanmar font, swap 2 GB (မရှိသေးရင်), `systemd` service, Nginx, အခမဲ့ HTTPS ကို လုပ်ပေးပါတယ်။

တန်ဖိုး လေးခုကို ကိုယ့်ဟာနဲ့ အစားထိုးပါ။ quote ကို မဖြုတ်ပါနဲ့။

```bash
sudo DOMAIN=recap.example.com \
  WEB_PASSWORD='ကိုယ်ရွေးတဲ့-စကားဝှက်-ရှည်' \
  GEMINI_API_KEY='key-ကို-ထည့်ပါ' \
  CERTBOT_EMAIL='you@example.com' \
  bash deploy/install.sh
```

အဆုံးမှာ `The web app is running.` နဲ့ `{"ok":true,...}` လို စာ ပေါ်ရပါမယ်။

ဒီ script ကို နောက်မှ ထပ် run လို့ရပါတယ်။ `.env`၊ တင်ထားတဲ့ ဗီဒီယို၊ လုပ်ပြီးသား အလုပ်တွေကို **မဖျက်ပါ**။

`WEB_PASSWORD` မထည့်ရင် script က စကားဝှက်တစ်ခု ဖန်တီးပြီး တစ်ခါပဲ ပြပါတယ်။ မှတ်ထားပါ။

ဖိုင်တွေက `/opt/recap-myanmar` မှာ ရှိပါတယ်။ Service အမည် `recap-web`။
Python က `127.0.0.1:8765` မှာပဲ နားထောင်ပါတယ်။ အပြင်ကနေ ဝင်တာ Nginx ကနေပါ။

### ၄။ ဖွင့်သုံးပါ

ဖုန်း ဒါမှမဟုတ် ကွန်ပျူတာ browser မှာ —

```text
https://recap.example.com
```

စကားဝှက် ရိုက်ပါ။ ပြီးရင် —

1. ဗီဒီယို ရွေးပါ (သို့) စာမျက်နှာပေါ် ဆွဲထည့်ပါ။ ဖိုင်ကြီးတွေ တင်လို့ရပါတယ် (8 GB အထိ)။
2. ဗီဒီယိုဘာသာ၊ မြန်မာအသံ၊ အမြန်နှုန်း၊ အချိုး၊ အရည်အသွေး ရွေးပါ။ CPU ၂ လုံးမှာ **720p** က မျှတပါတယ်။ **480p** က အမြန်ဆုံးပါ။
3. **Recap စမယ် / ဆက်မယ်** နှိပ်ပါ။ ဖုန်း သော့ခတ်ထားရင်လည်း server ပေါ်မှာ ဆက်လုပ်နေပါတယ်။ နောက်မှ ပြန်ဖွင့်ပြီး တိုးတက်မှု ကြည့်၊ ဒေါင်းလုဒ် လုပ်ပါ။
4. ဘာသာပြန်အဆင့် ပြီးရင် **မြန်မာစာ ပြင်ရန်** ပေါ်ပါတယ်။ စာကြောင်း ပြင်၊ သိမ်း၊ ပြီးရင် Start / Resume။ ပြင်ထားတဲ့ စာကြောင်းပဲ အသံပြန်ထုတ်ပါတယ်။
5. mp4 (srt, စာသား, plan ပါ) ကို အခြေအနေအကွက်ကနေ ဒေါင်းလုဒ်လုပ်ပါ။
6. **Splitter** tab က ဗီဒီယိုကို အပိုင်းခွဲပါတယ်။ **Jobs** မှာ အရင်အလုပ်တွေ ရှိပါတယ်။

တစ်ချိန်တည်းမှာ အလုပ် **တစ်ခု** ပဲ လုပ်ပါတယ်။ ကျန်တာက တန်းစီနေပါတယ် (အများဆုံး ၈ ခု)။

ဘာသာတစ်ခုကို ပထမဆုံးအကြိမ် လုပ်ရင် Whisper model ကို ဒေါင်းလုဒ်လုပ်ပါတယ် (English သို့ `small` ဆို ၅၀၀ MB လောက်၊ `medium` ဆို ၁.၅ GB လောက်)။ Log မှာ ပြပါတယ်။ နောက်တစ်ခါ ထပ်မယူပါ။

### ၅။ `.env` ထဲက အချက်များ

```bash
sudo nano /opt/recap-myanmar/.env
sudo systemctl restart recap-web
```

| အမည် | အဓိပ္ပာယ် |
|---|---|
| `WEB_PASSWORD` | ဝင်တဲ့ စကားဝှက် |
| `GEMINI_API_KEY` | ဘာသာပြန် (Gemini အသံ ရွေးရင် အဲ့ဒီအတွက်ပါ) |
| `GEMINI_IMAGE_PROJECT_1_KEY` | Key ထပ်ထည့်ချင်ရင်။ တစ်ခု ကုန်ရင် နောက်တစ်ခု။ Desktop app နဲ့ အတူတူ |
| `GEMINI_PROXY` | Gemini က server ရဲ့ နိုင်ငံကို လက်မခံမှ ထည့်ပါ။ ဥပမာ `http://127.0.0.1:10808`။ `none` ဆို အမြဲ တိုက်ရိုက် |
| `EDGE_TTS_PROXY` | ပုံမှန် ဗလာထားပါ။ Microsoft က VPN server တော်တော်များများကို ပိတ်ပါတယ် |
| `HF_ENDPOINT` | huggingface.co မဖွင့်မှ။ ဥပမာ `https://hf-mirror.com` |
| `WEB_COOKIE_SECURE` | HTTPS ပြီးမှ installer က `1` လုပ်ပေးပါတယ်။ `http://` နဲ့ သုံးနေတုန်း ဖွင့်ထားရင် စကားဝှက် မှတ်မထားပါ |

`.env` ကို ဘယ်သူ့ကိုမှ မပို့ပါနဲ့။ GitHub ပေါ်လည်း မတင်ပါနဲ့။ Key နဲ့ စကားဝှက် ပါတယ်။

English မဟုတ်တဲ့ ဗီဒီယိုအတွက် Whisper model ကို ဖိုင်မပြင်ဘဲ website က **Whisper (English မဟုတ်ရင်)** မှာ ပြောင်းပါ။ ဒီ server အတွက် ပုံမှန်က `small` ပါ။

### ၆။ နောက်မှ update လုပ်မယ်

VPS ပေါ်က clone လုပ်ထားတဲ့ folder မှာ —

```bash
cd ~/YOUR_REPO
git pull
sudo bash deploy/install.sh
```

စကားဝှက်က `/opt/recap-myanmar/.env` ထဲမှာ ရှိပြီးသားဆို ထပ်မပေးရပါဘူး။

### ၇။ ပြဿနာ တွေ့ရင်

| တွေ့တာ | လုပ်ရမယ့်အရာ |
|---|---|
| Site မပွင့်ဘူး | `ping` လုပ်ပြီး IP က VPS နဲ့ တူမတူ ကြည့်ပါ။ Server ပေါ်မှာ `sudo systemctl status recap-web nginx` |
| `502 Bad Gateway` | Python service ရပ်နေတယ်။ `sudo journalctl -u recap-web -n 80 --no-pager` ပြီး `sudo systemctl restart recap-web` |
| စကားဝှက် ရိုက်ပြီး စာမျက်နှာ အစကို ပြန်ရောက်နေတယ် | HTTPS ပြီးသွားမှ `http://` နဲ့ ဖွင့်နေလို့ပါ။ `https://` သုံးပါ။ သို့မဟုတ် စကားဝှက် မှားနေတယ် |
| Log မှာ `Gemini API key` | `/opt/recap-myanmar/.env` ထဲ key ထည့်ပြီး `sudo systemctl restart recap-web` |
| “Whisper model ဒေါင်းနေတယ်” ဆိုပြီး ကြာနေတယ် | ပထမတစ်ခါပဲ ကြာတာပါ။ စောင့်ပါ။ Log က စက္ကန့်အနည်းငယ်ခြားပြီး ပြပါတယ်။ huggingface.co ပိတ်နေရင် `HF_ENDPOINT` ထည့်ပါ |
| အသံထုတ်တာ 403 | **Server** ပေါ်က VPN ကို ပိတ်ပါ။ Microsoft က VPN လိပ်စာ အများကြီးကို ငြင်းပါတယ် |
| Gemini က နိုင်ငံကို လက်မခံဘူး | Gemini လက်ခံတဲ့ နိုင်ငံက proxy ကို `GEMINI_PROXY` မှာ ထည့်ပြီး restart |
| ဗီဒီယိုပေါ်က မြန်မာစာက အကွက်လွတ် | `sudo apt install fonts-noto-core` ပြီး `sudo systemctl restart recap-web`။ ခေါင်းစဉ်စာက Playwright Chromium လိုပါတယ်။ Installer က ထည့်ပေးပြီးပါပြီ |
| ဖိုင်ကြီး တင်လို့ မရဘူး | Nginx က ဒီ installer ရဲ့ site ဖြစ်ရပါမယ် (`client_max_body_size 8G`)။ `sudo nginx -t` |
| Disk ပြည့်နေတယ် | အလုပ်ဟောင်းတွေက `/opt/recap-myanmar/jobs`၊ တင်ထားတဲ့ဖိုင်က `web_data/uploads`။ Jobs tab မှာ ဖျက်ပြီး ဖိုင်ပါဖျက်မလားလို့ မေးရင် OK နှိပ်ပါ |
| Log တိုက်ရိုက် ကြည့်မယ် | `sudo journalctl -u recap-web -f` |

သုံးလေ့ရှိတဲ့ command —

```bash
sudo systemctl restart recap-web
sudo systemctl status recap-web
curl -s http://127.0.0.1:8765/healthz
```

`healthz` က server ထဲမှာပဲ ဖြေပါတယ်။ အပြင်ကနေ ဖွင့်တာ domain နဲ့ပါ။

### Desktop ပြတင်းပေါက်ထဲက အကုန် website မှာ မပါတာ

- Blur box ကို ထောင့်ဆွဲချဲ့တာ မပါပါ။ အကွက်ထည့်ပြီး ပုံပေါ် ဆွဲရွှေ့လို့ရပါတယ်။
- Folder တစ်ခုလုံးကို တစ်ခါတည်း batch လုပ်တာ မပါပါ။ ဗီဒီယို တစ်ခုပြီးတစ်ခု တင်ပါ။ တန်းစီပြီး တစ်ခုချင်း လုပ်ပါတယ်။
- Gemini `.env` က server ပေါ်က ဖိုင်ပဲ သုံးပါတယ်။ တခြား `.env` ကို လိုက်ရွေးစရာ မလိုပါ။
- Server restart ဖြစ်ရင် ရပ်သွားတဲ့ အလုပ်က အလိုလို မဆက်ပါ။ Site ဖွင့်ပြီး Start / Resume နှိပ်ပါ။ လုပ်ပြီးသား အဆင့်တွေ ကျန်နေပါတယ်။
