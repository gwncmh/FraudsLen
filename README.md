# FraudLens – mã nguồn theo kiến trúc đã chốt (Nhóm 10, Chủ đề 5)

```
Nguồn giao dịch ──► API (làn nhanh) ──► Scorer (XGBoost) ─┐
   (simulator)        │  bỏ định danh, kiểm tra,           │ timeout + ngắt mạch
                      │  lịch sử thẻ (Redis), feature,      │
                      │  phân luồng fail-safe               ▼
                      └──► PostgreSQL: 1 transaction ghi giao dịch, điểm, phân luồng, hồ sơ, outbox, audit
                                 │
       LLM worker (làn chậm) ◄───┘ outbox ──► BM25 playbook ──► LLM ──► kiểm tra bằng code ──► brief / brief mẫu
       Dashboard React ──► chỉ gọi API          Giám sát: PSI + chất lượng trên nhãn trễ ──► Prometheus/Grafana
```

## 1. Thư mục

| Thư mục / file | Thành phần trong báo cáo |
| --- | --- |
| `fraudlens\common\pii.py` | Bảo vệ số thẻ (HMAC), bỏ thông tin định danh |
| `fraudlens\common\validation.py` | Kiểm tra dữ liệu đầu vào (FR-01) |
| `fraudlens\common\features.py` | **Một hàm tính feature** dùng chung cho huấn luyện và chạy thật |
| `fraudlens\common\routing.py` | Phân luồng fail-safe |
| `fraudlens\common\db.py` | PostgreSQL + audit có chuỗi băm |
| `fraudlens\common\registry.py` | Model registry + cấu hình ngưỡng có phiên bản |
| `fraudlens\ml\` | Huấn luyện XGBoost, hiệu chỉnh Platt, chọn ngưỡng theo năng lực review, duyệt, chạy test một lần |
| `fraudlens\scorer\app.py` | Scorer – tiến trình riêng |
| `fraudlens\api\` | API: làn nhanh, Redis, gọi Scorer có ngắt mạch, endpoint cho dashboard |
| `fraudlens\worker\` | LLM worker: outbox, BM25, gọi LLM, kiểm tra brief, brief mẫu |
| `fraudlens\monitor\` | Giám sát PSI và chất lượng trên nhãn trễ |
| `fraudlens\tools\` | Simulator, nạp lịch sử, lệnh quản trị (truy vết, kiểm tra audit, kiểm tra LLM) |
| `frontend\` | Dashboard React |
| `db\schema.sql` | Lược đồ cơ sở dữ liệu |
| `kb\playbook.md` | Playbook bản nháp (thay khi có playbook chính thức) |
| `tests\` | Kiểm thử không cần cơ sở dữ liệu |

## 2. Máy cần gì

| | Tối thiểu | Khuyến nghị |
| --- | --- | --- |
| Hệ điều hành | Windows 10 (bản 22H2) hoặc Windows 11, 64-bit | Windows 11 |
| CPU | 4 nhân | 6–8 nhân |
| RAM | 8 GB (huấn luyện với `--sample-cards 0.5`) | 16 GB (huấn luyện toàn bộ dữ liệu) |
| Đĩa trống | 10 GB | 15 GB |
| GPU | không cần | không cần |

Khi hệ thống chạy, các container chiếm khoảng 3,5 GB RAM (PostgreSQL 768 MB, Redis 512 MB, API 768 MB, Scorer 512 MB,
worker và monitor mỗi cái 384 MB, giao diện ~50 MB). Bật thêm Prometheus + Grafana tốn thêm khoảng 0,8 GB.
Máy 8 GB nên đóng bớt trình duyệt và ứng dụng khác khi chạy.

> Huấn luyện chạy **ngoài Docker** (bằng Python trên máy) để dùng hết CPU/RAM; các dịch vụ chạy **trong Docker**.
> Các lệnh `python ...` chạy trên máy, các lệnh `docker compose ...` điều khiển container – tất cả đều gõ trong cùng một cửa sổ PowerShell.

## 3. Cài phần mềm (làm một lần)

1. **Python 3.11** (3.10–3.13 đều được): tải bản *Windows installer (64-bit)* ở python.org.
   Ở màn hình đầu của bộ cài, **tích ô "Add python.exe to PATH"** rồi chọn *Install Now*.
2. **Docker Desktop**: tải ở docker.com, cài với tùy chọn mặc định (WSL 2), khởi động lại máy nếu được yêu cầu.
   Mở Docker Desktop, chờ góc dưới bên trái báo **Engine running**. Vào *Settings → Resources*, cho Docker dùng ít nhất 4 GB RAM.
3. **Node.js**: không cần – giao diện React được build bên trong Docker.

Kiểm tra: mở PowerShell (Start → gõ `PowerShell` → Enter) và chạy

```powershell
python --version
docker --version
docker compose version
```

Cả ba phải in ra số phiên bản. Nếu `python` mở Microsoft Store, nghĩa là chưa tích "Add to PATH" – cài lại Python.

## 4. Chạy toàn bộ pipeline, từng bước

Ví dụ dưới đây giả sử dự án nằm ở `C:\Users\PC\GitHub\fraudlens`. Thay bằng đường dẫn thật của bạn.

### Bước 0 – Mở PowerShell tại thư mục dự án và đặt dữ liệu

```powershell
cd C:\Users\PC\GitHub\fraudlens
dir
```

Lệnh `dir` phải liệt kê `README.md`, `docker-compose.yml`, `requirements.txt` và các thư mục `fraudlens`, `frontend`, `data`...

Tải bộ *Credit Card Transactions Fraud Detection* (Sparkov) trên Kaggle (`kartik2112/fraud-detection`), giải nén,
đặt hai file đúng vị trí (không để trong thư mục con):

```
C:\Users\PC\GitHub\fraudlens\data\fraudTrain.csv
C:\Users\PC\GitHub\fraudlens\data\fraudTest.csv
```

> **Mọi lệnh từ đây trở đi chạy trong PowerShell đang đứng ở thư mục dự án.** Mở cửa sổ mới thì phải `cd` lại
> và kích hoạt lại môi trường Python (bước 1).

### Bước 1 – Môi trường Python (làm một lần)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Nếu báo *"running scripts is disabled on this system"*, chạy lệnh sau, gõ `Y`, rồi kích hoạt lại:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.venv\Scripts\Activate.ps1
```

Kích hoạt thành công thì đầu dòng lệnh có **`(.venv)`**. Cài thư viện (3–5 phút):

```powershell
pip install -r requirements.txt
```

Mỗi lần mở PowerShell mới: `cd C:\Users\PC\GitHub\fraudlens` rồi `.venv\Scripts\Activate.ps1`.

### Bước 2 – File cấu hình `.env` (làm một lần)

```powershell
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
notepad .env
```

Trong Notepad, thay giá trị sau `HMAC_KEY=` bằng chuỗi 64 ký tự vừa in ra, lưu (Ctrl+S) và đóng.
**Không đổi khóa này sau khi đã nạp dữ liệu**, vì lịch sử thẻ được gom theo mã băm (nếu lỡ đổi, xem mục 7 "Làm lại từ đầu").

### Bước 3 – Kiểm thử nhanh (không cần cơ sở dữ liệu)

```powershell
pytest -q
```

Dòng cuối phải là `... passed`, không có `failed`. Các test kiểm tra: feature không dùng dữ liệu tương lai,
offline = online, phân luồng an toàn khi lỗi, validator, ranh giới PII, kiểm tra brief, ngắt mạch,
model lưu ra và Scorer nạp lại cho cùng điểm.

### Bước 4 – Huấn luyện model

```powershell
# máy 16 GB: toàn bộ dữ liệu, kèm mốc so sánh luật viết tay và Logistic Regression
python -m fraudlens.ml.train --baselines

# máy 8 GB hoặc muốn thử nhanh: 50% số thẻ (hết RAM thì giảm xuống 0.3)
python -m fraudlens.ml.train --sample-cards 0.5
```

Lần đầu chương trình đọc CSV, tối thiểu hóa dữ liệu, tính feature cho ~1,85 triệu giao dịch rồi lưu cache vào
`data\cache\` (lần sau chỉ vài giây). Ước lượng trên laptop 4 nhân: 10–20 phút chuẩn bị dữ liệu lần đầu,
3–10 phút huấn luyện. Trong lúc chạy máy có thể chậm, đừng đóng cửa sổ.

Kết quả in ra Recall@1%, PR-AUC trên val_A/val_B, số đo phân luồng trên val_B và **tên phiên bản** dạng
`xgb-fs-v1-20261028...-abcd1234`. Model được ghi vào `registry\<phiên bản>\` nhưng **chưa được dùng** cho tới khi Risk Lead duyệt.

Tùy chọn hữu ích:

| Tùy chọn | Ý nghĩa |
| --- | --- |
| `--review-frac 0.01` | Ngân sách review 1% (benchmark đề bài) |
| `--capacity-per-day 900` | Năng lực review tính bằng số hồ sơ/ngày (thay cho phần trăm, xem mục 5.4 báo cáo) |
| `--high-precision 0.8` | Precision tối thiểu của nhóm HIGH |
| `--n-jobs 4` | Số luồng CPU khi huấn luyện |

### Bước 5 – Risk Lead duyệt ngưỡng và kích hoạt model

```powershell
python -m fraudlens.ml.approve --list
python -m fraudlens.ml.approve --version <phiên bản vừa in ra> --by "Ten Risk Lead"
```

Lệnh này ghi người duyệt vào `routing_config.json` và tạo `registry\ACTIVE`. **Phải làm trước khi khởi động Docker**,
nếu không Scorer sẽ khởi động lại liên tục. API từ chối dùng cấu hình chưa duyệt (khi đó mọi giao dịch thành UNSCORED,
đúng nguyên tắc an toàn khi lỗi).

### Bước 6 – Khởi động hệ thống

Mở Docker Desktop, chờ **Engine running**, rồi:

```powershell
docker compose up -d --build
docker compose ps
```

Lần build đầu mất 5–15 phút. `docker compose ps` phải thấy `postgres`, `redis`, `scorer`, `api`, `worker`, `monitor`,
`frontend` ở trạng thái `running` (postgres và redis kèm `healthy`).

Kiểm tra bằng trình duyệt: http://localhost:8000/health và http://localhost:8001/health phải trả về JSON có `"ok"`.
Service nào không chạy thì xem log, ví dụ `docker compose logs api --tail 50`.

### Bước 7 – Nạp lịch sử 30 ngày trước thời điểm phát lại

Simulator phát lại `fraudTest.csv` (bắt đầu 21/06/2020). Để feature của những giao dịch đầu tiên đúng, nạp 30 ngày cuối
của `fraudTrain.csv` làm lịch sử. Lệnh chạy bằng Python trên máy, kết nối vào PostgreSQL/Redis trong Docker:

```powershell
python -m fraudlens.tools.seed_history --csv data/fraudTrain.csv --days 30
```

Kết quả đúng: nạp khoảng 84 nghìn giao dịch và dựng Redis với số bản ghi tương đương.

> Nếu trước đây bạn dùng bản cũ và thấy dòng "nạp 1,296,675 giao dịch" (lỗi lọc ngày với pandas 3), xóa phần thừa bằng:
> `docker compose exec postgres psql -U fraudlens -d fraudlens -c "DELETE FROM transactions WHERE status='SEED' AND event_ts < (SELECT max(event_ts) FROM transactions WHERE status='SEED') - 30*86400;"`

### Bước 8 – Phát lại giao dịch

```powershell
# chạy thử: 2.000 giao dịch, 20 giao dịch/giây, 2% payload lỗi, nhãn trễ 7 ngày (~2 phút)
python -m fraudlens.tools.simulator --limit 2000 --tps 20

# chạy tiếp phần sau của file (~7 phút)
python -m fraudlens.tools.simulator --offset 2000 --limit 20000 --tps 50
```

Cuối mỗi lần chạy, simulator in độ trễ p50/p95/p99, số giao dịch theo luồng và số payload lỗi đã tiêm.
Kết quả đúng: số giao dịch UNSCORED bằng số lỗi tiêm vào trừ lỗi `duplicate` (bản gửi trùng nhận lại kết quả cũ),
không có giao dịch lỗi nào thành LOW.

### Bước 9 – Làm việc trên dashboard

Mở http://localhost:3000, đăng nhập bằng token trong `.env`:
`analyst-token` (Fraud Analyst), `lead-token` (Risk Lead), `admin-token` (Admin).

Hàng đợi xếp HIGH → MEDIUM → UNSCORED, trong mỗi mức theo điểm rồi số tiền. Mở hồ sơ để xem giao dịch, điểm, ngưỡng,
yếu tố ảnh hưởng, Case Brief; ghi nhận định, điều tra thêm, chuyển cấp hoặc đóng (đóng bắt buộc có nhận định).

### Bước 10 – Bật LLM soạn Case Brief

Không cấu hình LLM thì worker dùng **brief dạng mẫu**, mọi bước khác vẫn chạy đủ. Hồ sơ **UNSCORED luôn dùng brief mẫu**
(không có điểm để giải thích) – đây là thiết kế, không phải lỗi. Code gọi mọi nhà cung cấp có API tương thích OpenAI,
nên đổi LLM chỉ cần sửa vài dòng trong `.env`. Chọn **một** trong ba cách:

**Cách A – Ollama chạy trên máy (không giới hạn lượt gọi, dữ liệu không rời máy, chậm hơn).**

1. Tải và cài Ollama for Windows ở ollama.com (cài xong tự chạy nền).
2. `ollama pull qwen2.5:3b` (máy 16 GB có thể dùng `qwen2.5:7b`, viết tốt hơn, tốn ~5 GB RAM).
3. Trong `.env`:
   ```
   LLM_BASE_URL=http://host.docker.internal:11434/v1
   LLM_API_KEY=ollama
   LLM_MODEL=qwen2.5:3b
   LLM_TEMPERATURE=0
   LLM_TIMEOUT_S=180
   LLM_MIN_INTERVAL_S=0
   ```
   `LLM_API_KEY` chỉ cần khác rỗng. Khi chạy các lệnh kiểm tra ở bước 5 bên dưới (chạy trên máy, ngoài Docker),
   tạm đổi dòng đầu thành `http://localhost:11434/v1`, kiểm tra xong đổi lại `host.docker.internal`.

**Cách B – Groq (cloud, miễn phí có giới hạn theo ngày, chất lượng tốt hơn).**

1. Đăng ký ở console.groq.com, tạo API key, xem hạn mức ở trang Limits.
2. Trong `.env`:
   ```
   LLM_BASE_URL=https://api.groq.com/openai/v1
   LLM_API_KEY=<key Groq>
   LLM_MODEL=openai/gpt-oss-120b
   LLM_MIN_INTERVAL_S=20
   ```

**Cách C – Gemini Flash-Lite (Google AI Studio).**

1. Tạo API key ở Google AI Studio.
2. Trong `.env`:
   ```
   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
   LLM_API_KEY=<key Gemini>
   LLM_MODEL=gemini-3.5-flash-lite
   LLM_TEMPERATURE=
   LLM_MIN_INTERVAL_S=4
   ```
   Lỗi 404 nghĩa là key không được dùng tên model đó – xem tên đúng bằng lệnh `list-models` bên dưới.

**Sau khi sửa `.env` (cả ba cách):**

```powershell
python -m fraudlens.tools.admin list-models --all     # tên model key/nhà cung cấp này cho dùng
python -m fraudlens.tools.admin test-llm              # phải in "OK"
python -m fraudlens.tools.admin test-brief            # soạn thử 1 brief, phải ra "brief_source": "llm"
docker compose up -d --force-recreate worker          # worker đọc lại .env
python -m fraudlens.tools.admin regen-briefs          # soạn lại brief cho hồ sơ MEDIUM/HIGH đang mở
docker compose logs worker -f                         # thấy "brief llm" là được; Ctrl+C để thoát
```

Ý nghĩa lỗi của `test-llm`: `HTTP 400/401/403` sai key · `HTTP 404` sai tên model · `HTTP 429` hết hạn mức (đợi rồi thử lại) ·
`ConnectError` sai `LLM_BASE_URL` hoặc Ollama chưa chạy. Nếu `test-brief` ra `fallback`, mục `guardrail.rejections`
ghi lý do brief bị bộ kiểm tra loại; dashboard cũng hiện lý do này dưới brief.

### Bước 11 – Giám sát

```powershell
docker compose --profile monitoring up -d
```

- Grafana: http://localhost:3001 (đăng nhập `admin` / `admin`, có thể bấm *Skip* khi được hỏi đổi mật khẩu),
  vào *Dashboards → FraudLens*: độ trễ p50/p95, giao dịch theo luồng, trạng thái chấm điểm, PSI theo feature,
  hồ sơ đang mở, brief theo nguồn, precision/recall trên nhãn trễ.
- Prometheus: http://localhost:9090.
- Service `monitor` tính PSI (1 ngày gần nhất so với lúc huấn luyện; 0,1 cảnh báo, 0,2 hành động) và chất lượng trên
  nhãn trễ mỗi 5 phút, ghi vào bảng `monitor_metrics`. Tính ngay một lần:
  ```powershell
  docker compose exec monitor python -m fraudlens.monitor.main --once
  ```

### Bước 12 – Kiểm thử các tình huống lỗi (chứng minh các yêu cầu chất lượng)

| Tình huống | Lệnh | Kết quả đúng |
| --- | --- | --- |
| Dữ liệu lỗi | simulator mặc định tiêm 2% payload lỗi | Hồ sơ UNSCORED, không có giao dịch lỗi nào thành LOW |
| Scorer chết | `docker compose stop scorer` rồi `python -m fraudlens.tools.simulator --offset 25000 --limit 200 --fault-rate 0` | Giao dịch thành UNSCORED, API vẫn trả lời nhanh (ngắt mạch). Khôi phục: `docker compose start scorer` |
| LLM lỗi | đặt `LLM_API_KEY` sai, `docker compose up -d --force-recreate worker` | Brief dạng mẫu; điểm và phân luồng không đổi |
| Worker chết giữa chừng | `docker compose kill worker`, đợi một lúc, `docker compose start worker` | Không mất hồ sơ: job quay lại hàng đợi sau `JOB_STALE_S` giây |
| Redis mất dữ liệu | `docker compose exec redis redis-cli FLUSHALL` rồi `python -m fraudlens.tools.admin rebuild-redis` | Lịch sử thẻ dựng lại từ PostgreSQL |
| PostgreSQL dừng | `docker compose stop postgres` rồi chạy simulator vài giao dịch | API trả HTTP 503, không có kết quả nào bị trả mà không lưu. Khôi phục: `docker compose start postgres` |
| Truy vết | `python -m fraudlens.tools.admin trace <trans_num>` (lấy mã trên dashboard) | Đủ chuỗi: giao dịch → điểm, model → ngưỡng → hồ sơ → brief → thao tác → audit |
| Kiểm tra audit | `python -m fraudlens.tools.admin verify-audit` | Chuỗi băm hợp lệ; sửa trực tiếp bảng audit bị trigger chặn |

### Bước 13 – Kiểm thử tải ở cao điểm Mức 2 (200 giao dịch/giây)

1. `notepad .env`, đổi `API_WORKERS=2` thành `API_WORKERS=4`, lưu.
2. Chạy:
   ```powershell
   docker compose up -d api
   python -m fraudlens.tools.simulator --offset 30000 --limit 20000 --tps 200 --threads 16 --fault-rate 0
   ```

So sánh số giao dịch/giây thực tế và p95 in ra với yêu cầu p95 ≤ 500 ms. Simulator chạy cùng máy nên cũng chiếm CPU;
kết quả trên laptop là mức sàn. Với nhiều tiến trình API, số đo Prometheus của API được tính riêng theo từng tiến trình.

### Bước 14 – Chạy test set một lần (chỉ khi đã chốt model)

```powershell
python -m fraudlens.ml.final_test --version <phiên bản ACTIVE>
```

Ghi `registry\<phiên bản>\test_results.json` và tạo `TEST_RUN.lock`; chạy lại sẽ bị từ chối.

## 5. Đổi model, đổi ngưỡng, quay lui

```powershell
python -m fraudlens.ml.train ...                                  # tạo phiên bản mới
python -m fraudlens.ml.approve --version <mới> --by "Risk Lead"   # hoặc <cũ> để quay lui
docker compose restart scorer api
```

API kiểm tra phiên bản model mà Scorer trả về phải khớp với cấu hình ngưỡng đã duyệt; nếu lệch, giao dịch thành UNSCORED.

## 6. Tắt, bật lại, làm lại từ đầu

| Muốn | Lệnh |
| --- | --- |
| Tắt hệ thống (giữ dữ liệu) | `docker compose down` |
| Bật lại lần sau | Mở Docker Desktop → PowerShell: `cd <thư mục dự án>`, `.venv\Scripts\Activate.ps1`, `docker compose up -d` |
| Xóa sạch dữ liệu và làm lại (ví dụ sau khi đổi `HMAC_KEY`) | `docker compose down -v`, `docker compose up -d --build`, rồi làm lại từ bước 7 |
| Áp dụng code mới sau khi chép đè file | `docker compose up -d --build` |

## 7. Sự cố thường gặp trên Windows

| Hiện tượng | Nguyên nhân / cách xử lý |
| --- | --- |
| `python` mở Microsoft Store | Chưa tích "Add python.exe to PATH" khi cài – cài lại Python |
| `running scripts is disabled` khi kích hoạt `.venv` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ModuleNotFoundError: fraudlens` | Không đứng ở thư mục dự án hoặc chưa kích hoạt `.venv` |
| `docker: error during connect` | Docker Desktop chưa mở hoặc chưa báo *Engine running* |
| `port is already allocated` (5432, 6379, 8000, 3000) | Máy đã có PostgreSQL/Redis/ứng dụng khác dùng cổng đó: tắt trong `services.msc`, hoặc đổi cổng trong `docker-compose.yml` |
| `HMAC_KEY chưa đặt` | Chưa tạo `.env` hoặc khóa ngắn hơn 16 ký tự |
| `FileNotFoundError ... fraudTrain.csv` | CSV không nằm đúng `data\fraudTrain.csv` / `data\fraudTest.csv` |
| Huấn luyện hết RAM / máy treo | Dùng `--sample-cards 0.3` hoặc `0.5`; đóng bớt ứng dụng |
| Scorer khởi động lại liên tục | Chưa có `registry\ACTIVE` – làm bước 4 và 5 trước khi `docker compose up` |
| Mọi giao dịch đều UNSCORED | Xem `docker compose logs api` và `docker compose logs scorer`: chưa duyệt ngưỡng, Scorer chưa chạy, hoặc model khác cấu hình |
| Brief luôn là dạng mẫu | Chạy `python -m fraudlens.tools.admin test-llm` và `test-brief` (bước 10); xem `docker compose logs worker` |
| Dashboard báo token không hợp lệ | Token phải khớp `DASHBOARD_TOKENS` trong `.env`; sửa xong chạy `docker compose up -d api` |

## 8. Những gì là bản đồ án, chưa phải bản vận hành thật

- Đăng nhập dashboard bằng token tĩnh trong `.env` (cơ chế thật sẽ chốt khi hiện thực; chỉ cần thay `fraudlens\api\auth.py`).
- Một máy, PostgreSQL và Redis là điểm lỗi đơn (Giai đoạn 1). Giai đoạn 2 trong báo cáo thêm bản sao và máy riêng.
- Playbook là bản nháp `draft-0`.
- Chuỗi băm audit dùng một khóa chung (đủ cho Mức 1–2; Mức 3 chuyển sang chuỗi theo phân vùng).
