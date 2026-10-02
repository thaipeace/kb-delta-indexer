# PART 1: CODING SPECIFICATION & IMPLEMENTATION GUIDE
## OptiBot Mini-Clone (RAG Pipeline, Vector Store & Automated Delta Job)

> **File:** `kb-delta-indexer/SPECIFICATION.md`  
> **Source Task:** OptiSigns Take-Home Test · Part 1 of 2  
> **Time Budget:** ~8 focused hours  
> **Pass Bar:** 70 / 75 points (Max points: 25 Scrape + 20 Vector Store + 15 Daily Job + 10 Code/README + 5 Bonus Tests)  
> **Primary AI Engine:** Google Gemini (100% Free via Google AI Studio File API / File Search) · Có hỗ trợ OpenAI Assistants API v2.

---

## 1. MỤC TIÊU VÀ CÁC NGUYÊN TẮC BẮT BUỘC (CONSTRAINTS)

### 1.1 Mục tiêu kỹ thuật
Xây dựng một pipeline RAG (Retrieval-Augmented Generation) hoàn chỉnh và có khả năng chạy tự động:
1. **Scrape & Normalize:** Cào $\ge 30$ bài viết từ `support.optisigns.com` và chuyển thành Markdown chuẩn, sạch sẽ.
2. **Vector Store Integration:** Upload các bài viết lên Google Gemini Knowledge Base / File API (hoặc OpenAI Vector Store) hoàn toàn bằng Python script qua API (nghiêm cấm kéo thả qua giao diện UI).
3. **Delta Sync Engine:** Phát hiện bài mới (`added`), bài cập nhật (`updated`), và bài không đổi (`skipped`) để chỉ đẩy phần delta lên Vector Store.
4. **Daily Containerized Job:** Đóng gói Docker (`Dockerfile`), cấu hình cron chạy hàng ngày trên cloud (GitHub Actions / Railway / Render) và ghi nhận log công khai.
5. **Assistant Verification:** Cấu hình OptiBot với prompt cố định và kiểm thử câu hỏi mẫu: *"How do I add a YouTube video?"*.

### 1.2 Các ràng buộc "sống còn" (Strict Rules)
* **Tên GitHub Repo:** Tuyệt đối **KHÔNG** đặt tên repo chứa chữ "optisigns" (để tránh các ứng viên tương lai tìm thấy). Đặt tên ẩn danh (ví dụ: `kb-delta-indexer`, `sync-agent-rag`, `docs-retrieval-service`).
* **Bảo mật:** Không commit API key. Bắt buộc có `.env.sample`.
* **Giao tiếp Docker:** Lệnh chạy bắt buộc:  
  `docker run -e GEMINI_API_KEY=... <image>` (hoặc `-e OPENAI_API_KEY=...`)  
  Script phải chạy 1 lần duy nhất, in log tổng kết và **exit 0**.
* **README:** Giới hạn $\le 1$ trang, bao gồm: Setup, Hướng dẫn chạy local, Link xem log daily job, và Screenshot trả lời câu hỏi kèm trích dẫn nguồn URL.

---

## 2. KIẾN TRÚC TOÀN BỘ HỆ THỐNG (SYSTEM ARCHITECTURE)

```
[support.optisigns.com (Zendesk)]
            │
            ▼ (HTTP GET /api/v2/help_center/en-us/articles.json)
┌────────────────────────────────────────────────────────────────────────┐
│ 1. DATA INGESTION & NORMALIZATION                                      │
│    - Fetch articles JSON (id, title, url, updated_at, body HTML)       │
│    - HTML Cleaner & Markdown Converter (markdownify)                   │
│    - Fix relative URLs -> Absolute URLs                                │
│    - Inject YAML Frontmatter (id, title, source_url, content_hash)     │
│    - Save to: data/articles/<slug>.md                                  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 2. INCREMENTAL DELTA ENGINE                                            │
│    - Read previous state: data/sync_state.json                         │
│    - Compute SHA-256 hash of cleaned markdown content                  │
│    - Compare (hash, updated_at):                                       │
│        • ADDED:   New article ID -> Flag for upload                    │
│        • UPDATED: Hash/updated_at changed -> Delete old & re-upload     │
│        • SKIPPED: Hash identical -> Bypass upload                      │
│    - Output log: [Scraped: X | Added: A | Updated: U | Skipped: S]     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Delta files only)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 3. VECTOR STORE & KNOWLEDGE BASE UPLOADER                              │
│    - Google Gemini File API (client.files.upload) [PRIMARY - FREE]     │
│    - Alternative: OpenAI Assistants API v2 (Vector Stores)             │
│    - Save remote_file_id back to sync_state.json                       │
│    - Configure Assistant with verbatim prompt                          │
│    - Log: Total files & chunk count embedded                           │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 4. RUNTIME, DOCKER & CI/CD CRON                                        │
│    - main.py: Orchestrates Step 1 -> 2 -> 3 -> Exits with code 0      │
│    - Dockerfile: python:3.11-slim container                            │
│    - .github/workflows/daily_sync.yml: Cron schedule 00:00 UTC daily   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. CHI TIẾT KỸ THUẬT TỪNG MODULE

### 3.1 Module 1: Ingestion & Markdown Converter (`src/scraper.py`)
Thay vì dùng Playwright/Selenium nặng nề và dễ gãy, sử dụng trực tiếp **Zendesk Public REST API**:
* **API URL:** `https://support.optisigns.com/api/v2/help_center/en-us/articles.json?per_page=100`
* **Response Payload Schema:**
  ```json
  {
    "articles": [
      {
        "id": 360012345678,
        "name": "How to set up YouTube App",
        "html_url": "https://support.optisigns.com/hc/en-us/articles/360012345678-How-to-set-up-YouTube-App",
        "updated_at": "2026-05-10T14:32:00Z",
        "body": "<p>Follow these steps...</p>"
      }
    ]
  }
  ```
* **Quy tắc làm sạch (HTML $\rightarrow$ Markdown):**
  1. Loại bỏ các thẻ script, style, navigation thừa.
  2. Dùng `markdownify` với cấu hình: `heading_style="ATX"` (`#`, `##`), giữ `code_language`, bảo toàn thẻ bảng (table) và danh sách.
  3. **Chuẩn hóa URL:** Quét tất cả thẻ `<a>` có đường dẫn tương đối (ví dụ: `/hc/en-us/...`) và tiền tố hóa thành `https://support.optisigns.com/hc/en-us/...`.
  4. Đính kèm **YAML Frontmatter** ở đầu mỗi file markdown để AI có ngữ cảnh siêu dữ liệu:
     ```markdown
     ---
     id: 360012345678
     title: "How to set up YouTube App"
     url: "https://support.optisigns.com/hc/en-us/articles/360012345678-How-to-set-up-YouTube-App"
     updated_at: "2026-05-10T14:32:00Z"
     ---

     # How to set up YouTube App

     Article URL: https://support.optisigns.com/hc/en-us/articles/360012345678-How-to-set-up-YouTube-App

     [Nội dung bài viết sạch]
     ```
  5. **Tên file:** Lưu vào `data/articles/{slug}.md` (slug được tạo từ `title` bằng thư viện `python-slugify`).

---

### 3.2 Module 2: State Engine & Delta Detection (`src/delta.py`)
Mục đích: Không bao giờ re-upload những bài không đổi, tiết kiệm chi phí API và ngăn rác vector store.

* **File lưu trạng thái:** `data/sync_state.json`
* **Cấu trúc JSON Schema:**
  ```json
  {
    "version": 1,
    "last_sync": "2026-10-01T17:00:00Z",
    "provider": "gemini",
    "vector_store_id": "gemini-store-001",
    "articles": {
      "360012345678": {
        "slug": "how-to-set-up-youtube-app",
        "title": "How to set up YouTube App",
        "url": "https://support.optisigns.com/hc/en-us/articles/360012345678",
        "updated_at": "2026-05-10T14:32:00Z",
        "content_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "remote_file_id": "files/abc123xyz"
      }
    }
  }
  ```
* **Thuật toán phân loại trạng thái (Delta Classification):**
  ```python
  def classify_article(article_id, new_hash, new_updated_at, state):
      if article_id not in state["articles"]:
          return "ADDED"
      current = state["articles"][article_id]
      if current["content_hash"] != new_hash or current["updated_at"] != new_updated_at:
          return "UPDATED"
      return "SKIPPED"
  ```
* **Xử lý khi UPDATED:**
  1. Xóa file cũ khỏi Google Gemini / Vector Store.
  2. Upload file markdown mới.
  3. Cập nhật `remote_file_id` và `content_hash` mới vào `sync_state.json`.

---

### 3.3 Module 3: Vector Store & Assistant Manager (`src/assistant.py`)

#### A. System Prompt bắt buộc (Verbatim - không được sửa một chữ):
```text
You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply.
```

#### B. Chiến lược Chunking & Rationale (Trình bày trong README)
* **Phương pháp:** Header-Aware Semantic Chunking kết hợp Recursive Character Splitting.
* **Thông số:**
  * Chunk Size: `800 tokens` (khoảng 3200 ký tự).
  * Chunk Overlap: `100 tokens` (khoảng 400 ký tự).
* **Lý do lựa chọn (Rationale):**
  Các bài hướng dẫn hỗ trợ kỹ thuật của OptiSigns thường là dạng danh sách các bước (Step 1, Step 2, Step 3...). Kích thước 800 tokens đủ lớn để chứa trọn vẹn một quy trình thao tác dưới một đề mục (`##`), trong khi 100 token overlap đảm bảo các câu chuyển đoạn và liên kết ngữ cảnh không bị đứt gãy giữa các chunk liền kề.

#### C. Triển khai với Google Gemini (Google AI Studio - 100% Free):
```python
from google import genai
from google.genai import types

client = genai.Client(api_key=GEMINI_API_KEY)

# 1. Upload delta markdown files lên Gemini File API
uploaded_file = client.files.upload(file=filepath)

# 2. Truy vấn có Grounding / System Prompt
response = client.models.generate_content(
    model="gemini-1.5-flash",
    contents=[uploaded_file, "How do I add a YouTube video?"],
    config=types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.2,
    )
)
```

---

### 3.4 Module 4: Sanity Test & Verification (`scripts/test_bot.py`)
* **Câu hỏi kiểm thử:** *"How do I add a YouTube video?"*
* **Tiêu chí kết quả đạt chuẩn:**
  1. Trả lời đúng các bước thêm ứng dụng YouTube trên OptiSigns CMS.
  2. Định dạng tối đa 5 gạch đầu dòng (bullet points).
  3. Có trích dẫn rõ ràng định dạng: `Article URL: https://support.optisigns.com/...` (tối đa 3 URLs).
* **Bằng chứng (Deliverable):** Chụp màn hình Google AI Studio hoặc terminal chạy script test và lưu vào `docs/sanity_test_result.png`.

---

### 3.5 Module 5: Orchestrator, Docker & Daily Cron (`main.py`, `Dockerfile`)

* **Luồng chạy của `main.py`:**
  ```python
  def main():
      logger.info("Step 1: Scraping articles from Zendesk...")
      articles = scrape_zendesk_articles(min_count=35)
      
      logger.info("Step 2: Detecting delta changes...")
      delta = compute_delta(articles)
      
      logger.info("Step 3: Syncing delta to Vector Store / Gemini Knowledge Base...")
      sync_results = sync_to_knowledge_base(delta)
      
      logger.info(
          f"[SYNC COMPLETE] Total Scraped: {len(articles)} | "
          f"Added: {sync_results.added} | "
          f"Updated: {sync_results.updated} | "
          f"Skipped: {sync_results.skipped} | "
          f"Total Remote Files: {sync_results.total_files}"
      )
      sys.exit(0)
  ```

* **Cấu hình `Dockerfile`:**
  ```dockerfile
  FROM python:3.11-slim
  WORKDIR /app
  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt
  COPY . .
  CMD ["python", "main.py"]
  ```

* **Tự động hóa với GitHub Actions (`.github/workflows/daily_sync.yml`):**
  ```yaml
  name: Daily Knowledge Base Sync
  on:
    schedule:
      - cron: '0 2 * * *'  # Chạy 2h sáng UTC mỗi ngày
    workflow_dispatch:      # Cho phép kích hoạt thủ công từ giao diện

  jobs:
    sync:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5
          with:
            python-version: '3.11'
        - name: Install dependencies
          run: pip install -r requirements.txt
        - name: Run Delta Sync Job
          env:
            GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          run: python main.py
        - name: Commit updated state
          run: |
            git config --local user.email "action@github.com"
            git config --local user.name "GitHub Action"
            git add data/sync_state.json
            git diff --quiet && git diff --staged --quiet || git commit -m "chore: update sync_state.json [skip ci]"
            git push
  ```

---

## 4. BỘ TEST CASES UNIT TEST (+5 ĐIỂM BONUS) (`tests/`)

Tạo thư mục `tests/` với các test case bằng `pytest`:
1. `test_scraper.py`:
   - Test hàm chuyển đổi HTML sang Markdown: Kiểm tra link relative chuyển thành absolute.
   - Kiểm tra loại bỏ thẻ script, style, nav bar.
2. `test_delta.py`:
   - Test phát hiện bài viết mới (`ADDED`).
   - Test phát hiện bài viết bị đổi nội dung (`UPDATED`).
   - Test bỏ qua bài viết nguyên vẹn (`SKIPPED`).
   - Test lưu trữ `remote_file_id`.

---

## 5. CẤU TRÚC THƯ MỤC DỰ ÁN ĐỀ XUẤT (REPO LAYOUT)

```
kb-delta-indexer/               # Tên repo kín, KHÔNG chứa "optisigns"
├── .github/
│   └── workflows/
│       └── daily_sync.yml      # CI/CD chạy cron hàng ngày
├── data/
│   ├── articles/               # Thư mục chứa các file markdown đã cào
│   │   ├── how-to-use-youtube.md
│   │   └── ...
│   └── sync_state.json         # File lưu delta state
├── docs/
│   └── sanity_test_result.png  # Ảnh chụp màn hình câu trả lời mẫu
├── src/
│   ├── __init__.py
│   ├── config.py               # Load biến môi trường (GEMINI_API_KEY) & constants
│   ├── scraper.py              # Zendesk API client & HTML2Markdown
│   ├── delta.py                # Hash calculator & State diffing
│   └── assistant.py            # Gemini File API & Vector Store
├── tests/
│   ├── test_scraper.py
│   └── test_delta.py
├── .env.sample                 # Mẫu GEMINI_API_KEY=...
├── .gitignore
├── Dockerfile                  # Container chạy 1 lần exit 0
├── main.py                     # Entrypoint điều phối toàn bộ job
├── README.md                   # Báo cáo kỹ thuật gọn gàng <= 1 trang
└── requirements.txt            # google-genai, openai, httpx, markdownify, python-slugify, pytest
```

---

## 6. CHECKLIST TỰ ĐÁNH GIÁ TRƯỚC KHI SUBMIT (PASS BAR: 70/75)

- [x] Đã cào tối thiểu **30 bài viết** từ `support.optisigns.com` (Thực tế: 101 bài).
- [x] File Markdown sạch sẽ, có YAML frontmatter, link hoạt động tốt, không rác HTML.
- [x] Chạy lại lần 2 in ra log: `Skipped: 101, Added: 0, Updated: 0` (chứng minh Delta hoạt động).
- [ ] Script Python tự động tạo/upload Knowledge Base qua API, không có thao tác thủ công.
- [ ] Prompt của Assistant giống 100% nguyên văn trong đề bài.
- [ ] Lệnh `docker run -e GEMINI_API_KEY=... <image>` chạy thông suốt và exit 0.
- [ ] Có ảnh chụp màn hình Assistant trả lời đúng câu hỏi YouTube kèm link nguồn.
- [ ] Tên repository **không chứa** chữ "optisigns".
- [ ] README ngắn gọn $\le 1$ trang, có đầy đủ link log và giải thích chiến lược chunking.
- [x] Chạy `pytest` passed 100% (+5 bonus points - 12/12 passed).
