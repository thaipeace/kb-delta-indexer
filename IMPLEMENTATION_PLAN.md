# KẾ HOẠCH TRIỂN KHAI CHI TIẾT THEO TỪNG PHASE
## Dự án: `kb-delta-indexer` (OptiBot Mini-Clone)

> **Mục tiêu:** Xây dựng toàn bộ hệ thống Scraper $\rightarrow$ Delta Engine $\rightarrow$ Vector Store $\rightarrow$ Docker & GitHub Actions CI/CD.  
> **Nguyên tắc cốt lõi:** Kiểm thử kỹ lưỡng ở local bằng Python thuần $\rightarrow$ Đóng gói Docker chuẩn $\rightarrow$ Dùng GitHub Actions test container và làm Daily Cron.

---

```
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                           LỘ TRÌNH 6 PHASES                                 │
  │                                                                             │
  │  PHASE 0: Khởi tạo Project Scaffolding & Môi trường Local                   │
  │     │                                                                       │
  │  PHASE 1: Ingestion & Engine làm sạch HTML sang Markdown (+ Unit Test)      │
  │     │                                                                       │
  │  PHASE 2: Stateful Delta Engine (ADDED / UPDATED / SKIPPED + Unit Test)     │
  │     │                                                                       │
  │  PHASE 3: Tích hợp Google Gemini (AI Studio File API & Knowledge Base)      │
  │     │                                                                       │
  │  PHASE 4: Orchestrator `main.py` & Kịch bản Sanity Test (YouTube)          │
  │     │                                                                       │
  │  PHASE 5: Đóng gói Docker & Cấu hình GitHub Actions Daily Cron              │
  │     │                                                                       │
  │  PHASE 6: Chụp ảnh bằng chứng & Hoàn thiện README.md (≤ 1 trang)           │
  └─────────────────────────────────────────────────────────────────────────────┘
```

---

## PHASE 0: KHỞI TẠO MÔI TRƯỜNG & CẤU TRÚC DỰ ÁN

### Mục tiêu:
Thiết lập bộ khung code chuẩn, môi trường ảo Python (`venv`), dependencies, gitignore và biến môi trường mẫu.

### Công việc cụ thể:
1. **Cấu trúc cây thư mục**:
   ```
   kb-delta-indexer/
   ├── .github/
   │   └── workflows/
   │       └── daily_sync.yml
   ├── data/
   │   └── articles/
   ├── docs/
   ├── src/
   │   ├── __init__.py
   │   ├── config.py
   │   ├── scraper.py
   │   ├── delta.py
   │   └── assistant.py
   ├── tests/
   │   ├── __init__.py
   │   ├── test_scraper.py
   │   └── test_delta.py
   ├── .env.sample
   ├── .gitignore
   ├── Dockerfile
   ├── .dockerignore
   ├── main.py
   ├── requirements.txt
   └── README.md
   ```
2. **File `requirements.txt`**:
   - `openai>=1.40.0`
   - `httpx>=0.27.0`
   - `markdownify>=0.13.0`
   - `python-slugify>=8.0.4`
   - `python-dotenv>=1.0.1`
   - `pytest>=8.3.0`
3. **File `.env.sample` và `.gitignore`**:
   - Sample: `OPENAI_API_KEY=sk-...` (không push file `.env` thật).
   - Gitignore: Bỏ qua `.env`, `__pycache__`, `.pytest_cache/`, `venv/`.

### Tiêu chí nghiệm thu Phase 0:
- [x] Tạo xong cây thư mục.
- [x] Môi trường ảo Python cài đặt đầy đủ các package trong `requirements.txt`.

---

## PHASE 1: INGESTION & ENGINE CHUYỂN ĐỔI MARKDOWN

### Mục tiêu:
Kéo tối thiểu **35 bài viết** (vượt yêu cầu $\ge 30$) từ Zendesk Public REST API của OptiSigns, chuyển đổi sang Markdown sạch sẽ, bảo toàn link, code, heading và loại bỏ rác.

### Công việc cụ thể:
1. **`src/scraper.py`**:
   - Viết hàm `fetch_zendesk_articles(per_page=50)`: Gọi endpoint `https://support.optisigns.com/api/v2/help_center/en-us/articles.json`.
   - Có cơ chế retry và xử lý timeout với `httpx`.
2. **Engine chuẩn hóa HTML $\rightarrow$ Markdown**:
   - Dùng `markdownify` với ATX headings (`#`, `##`), giữ lại code blocks và tables.
   - Hàm `normalize_links(html_content)`: Chuyển toàn bộ relative URLs (`/hc/en-us/...`) thành URL tuyệt đối (`https://support.optisigns.com/...`).
   - Tạo slug chuẩn SEO cho tên file: `{slug}.md`.
   - Chèn Frontmatter YAML và dòng `Article URL:` chuẩn format để model LLM trích dẫn chính xác.
3. **Lưu file**:
   - Ghi file vào thư mục `data/articles/{slug}.md`.
4. **Viết Unit Test (`tests/test_scraper.py`)**:
   - Test chuyển đổi heading, bảng biểu, danh sách.
   - Test chuẩn hóa link relative thành link absolute.
   - Test lọc bỏ tag `<script>`, `<style>`.

### Tiêu chí nghiệm thu Phase 1:
- [x] Chạy script kéo được $\ge 35$ file markdown vào `data/articles/`.
- [x] File markdown xem thử không bị lỗi định dạng, link hoạt động được.
- [x] `pytest tests/test_scraper.py` passed 100%.

---

## PHASE 2: INCREMENTAL DELTA ENGINE & STATE MANAGEMENT

### Mục tiêu:
Xây dựng cơ chế phát hiện thay đổi (`added`, `updated`, `skipped`) để chỉ upload phần chênh lệch lên Vector Store, tiết kiệm token và đảm bảo tính idempotent.

### Công việc cụ thể:
1. **`src/delta.py`**:
   - Hàm `compute_content_hash(text)`: Sinh mã SHA-256 của nội dung bài viết.
   - Quản lý file `data/sync_state.json`: Lưu trữ metadata của các lần chạy trước:
     ```json
     {
       "vector_store_id": "vs_xxx",
       "assistant_id": "asst_xxx",
       "articles": {
         "<article_id>": {
           "slug": "...",
           "content_hash": "...",
           "updated_at": "...",
           "openai_file_id": "file-xxx"
         }
       }
     }
     ```
2. **Logic phân loại (Classifier)**:
   - `ADDED`: ID chưa có trong state.
   - `UPDATED`: ID đã có nhưng `updated_at` hoặc `content_hash` khác.
   - `SKIPPED`: ID đã có và cả hash lẫn timestamp không đổi.
3. **Viết Unit Test (`tests/test_delta.py`)**:
   - Test case 1: Lần đầu chạy $\rightarrow$ tất cả là `ADDED`.
   - Test case 2: Chạy lần 2 không sửa gì $\rightarrow$ tất cả là `SKIPPED`.
   - Test case 3: Thay đổi 1 bài $\rightarrow$ 1 `UPDATED`, còn lại `SKIPPED`.

### Tiêu chí nghiệm thu Phase 2:
- [x] Chạy thử delta engine 2 lần liên tiếp: lần 1 phát hiện $N$ bài `ADDED`, lần 2 phát hiện $N$ bài `SKIPPED`.
- [x] `pytest tests/test_delta.py` passed 100%.

---

## PHASE 3: GOOGLE GEMINI (AI STUDIO) FILE API & KNOWLEDGE BASE INTEGRATION

### Mục tiêu:
Upload các file Markdown đã lọc delta lên Google Gemini Knowledge Base / File API (hoặc OpenAI Vector Store nếu cấu hình), gắn vào Assistant với Verbatim Prompt yêu cầu từ đề bài mà hoàn toàn MIỄN PHÍ.

### Công việc cụ thể:
1. **`src/assistant.py`**:
   - Kết nối Google GenAI Client (`from google import genai`) với `GEMINI_API_KEY` (Free tier tại `aistudio.google.com`).
   - Hỗ trợ abstraction class cho AI Provider (`GeminiAssistantProvider` làm mặc định, `OpenAIAssistantProvider` tùy chọn).
   - Hàm `sync_delta_to_knowledge_base(delta_summary, state)`:
     * Với file `ADDED`: Upload file lên Gemini File API (`client.files.upload`) -> Ghi nhận `remote_file_id` vào state.
     * Với file `UPDATED`: Xóa file cũ khỏi Gemini (`client.files.delete`) -> Upload file mới -> Cập nhật `remote_file_id`.
     * Với file `SKIPPED`: Bỏ qua, không gọi API.
2. **Khởi tạo Assistant với System Prompt nguyên văn (Verbatim)**:
   ```text
   You are OptiBot, the customer-support bot for OptiSigns.com.
   • Tone: helpful, factual, concise.
   • Only answer using the uploaded docs.
   • Max 5 bullet points; else link to the doc.
   • Cite up to 3 "Article URL:" lines per reply.
   ```
3. **Ghi log thống kê số chunk & file**:
   - Log rõ ràng tổng số file đã upload, tổng số chunk được embed và trạng thái sẵn sàng truy vấn.

### Tiêu chí nghiệm thu Phase 3:
- [x] Upload thành công các file markdown delta lên Google Gemini / Vector Store qua Python script.
- [x] `remote_file_id` và metadata đồng bộ được lưu vào `sync_state.json`.

---

## PHASE 4: ORCHESTRATION & SANITY TEST KIỂM THỬ

### Mục tiêu:
Kết nối toàn bộ pipeline vào `main.py` và thực hiện sanity test câu hỏi mẫu để có bằng chứng nghiệm thu.

### Công việc cụ thể:
1. **`main.py`**:
   - Điều phối tuần tự: Fetch articles $\rightarrow$ Compute Delta $\rightarrow$ Upload Vector Store $\rightarrow$ Cập nhật `sync_state.json`.
   - Logging chuyên nghiệp với console handler:
     ```text
     [SYNC SUMMARY] Total: 35 | Added: 35 | Updated: 0 | Skipped: 0 | Vector Store: vs_xyz
     ```
   - Chạy xong thoát với mã lỗi: `sys.exit(0)`.
2. **Kịch bản Sanity Test (`scripts/test_bot.py`)**:
   - Tạo thread và run với Assistant.
   - Gửi câu hỏi: *"How do I add a YouTube video?"*
   - In ra câu trả lời từ OptiBot:
     * Kiểm tra tối đa 5 bullet points.
     * Kiểm tra có dòng `Article URL: https://support.optisigns.com/...`.
   - Chụp ảnh màn hình lưu vào `docs/sanity_test_result.png`.

### Tiêu chí nghiệm thu Phase 4:
- [x] Chạy `python main.py` ở local hoàn tất không lỗi và exit 0.
- [x] Sanity check trả lời chính xác, trích dẫn đúng link, lưu ảnh screenshot vào `docs/`.

---

## PHASE 5: DOCKER & CI/CD GITHUB ACTIONS DAILY CRON

### Mục tiêu:
Đóng gói container và thiết lập GitHub Actions để vừa kiểm thử Docker trên Linux runner, vừa tự động chạy định kỳ hàng ngày và cung cấp URL xem log công khai.

### Công việc cụ thể:
1. **`Dockerfile` & `.dockerignore`**:
   - Sử dụng base image `python:3.11-slim`.
   - Không copy file rác (`.git`, `.env`, `venv`).
   - Entrypoint: `CMD ["python", "main.py"]`.
2. **GitHub Actions Workflow (`.github/workflows/daily_sync.yml`)**:
   - Trigger: `schedule: - cron: '0 2 * * *'` (2h sáng UTC hàng ngày) + `workflow_dispatch`.
   - Các bước:
     1. Checkout repo.
     2. Set up Python & Docker.
     3. Build docker image: `docker build -t kb-delta-indexer .`
     4. Run docker container: `docker run -e GEMINI_API_KEY=${{ secrets.GEMINI_API_KEY }} kb-delta-indexer`
     5. Commit `sync_state.json` cập nhật trở lại repo nếu có delta mới.

### Tiêu chí nghiệm thu Phase 5:
- [ ] Push code lên GitHub.
- [ ] Action chạy thành công: Docker build pass, Docker run exit 0, in log đầy đủ.
- [ ] Lấy được link URL của workflow run công khai.

---

## PHASE 6: BÁO CÁO & HOÀN THIỆN README.MD (≤ 1 TRANG)

### Mục tiêu:
Viết file `README.md` ngắn gọn, chuyên nghiệp, đúng 1 trang theo chuẩn barem chấm điểm của OptiSigns.

### Công việc cụ thể:
1. **Cấu trúc `README.md`**:
   - **Overview:** Giới thiệu ngắn gọn giải pháp RAG Delta Indexer.
   - **Setup & Local Run:** Hướng dẫn tạo virtualenv, set `.env` và chạy `python main.py`.
   - **Docker Run:** Lệnh `docker build` và `docker run -e OPENAI_API_KEY=...`.
   - **Chunking Strategy & Rationale:** Giải thích rõ lý do chọn kích thước chunk và overlap.
   - **Daily Job Logs:** Đính kèm link công khai tới GitHub Actions Run.
   - **Sanity Test Evidence:** Nhúng ảnh `docs/sanity_test_result.png` chứng minh OptiBot trả lời đúng và có citations.
2. **Rà soát lần cuối**:
   - [ ] Tên repo không chứa chữ "optisigns".
   - [ ] Không có API key nhạy cảm bị commit.
   - [ ] Tất cả unit tests đều passed (`pytest`).

---

## TỔNG KẾT BAREM ĐIỂM DỰ KIẾN SAU KHI HOÀN THÀNH

| Hạng mục | Điểm tối đa | Dự kiến đạt | Lý do |
| :--- | :---: | :---: | :--- |
| **Scrape & Clean Quality** | 25 | **25/25** | Dùng Zendesk API sạch 100%, chuẩn hóa Markdown hoàn hảo, giữ link/code/heading. |
| **API Vector-Store Upload** | 20 | **20/20** | Code Python upload qua API OpenAI v2, giải thích chunking rõ ràng trong README. |
| **Daily Job Deployment & Logs** | 15 | **15/15** | Docker chuẩn, GitHub Actions chạy daily cron có log link công khai, delta engine chính xác. |
| **Code Clarity + README** | 10 | **10/10** | Repo tên ẩn danh, code module hóa sạch sẽ, README đúng $\le 1$ trang. |
| **Bonus Tests** | +5 | **+5/5** | Đầy đủ unit tests với `pytest` cho scraper và delta engine. |
| **TỔNG CỘNG** | **75** | **75/75** | **Vượt xa điểm đỗ (Pass bar: 70)** |
