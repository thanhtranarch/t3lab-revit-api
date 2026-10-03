# T3Lab Assistant × LLMs Setting — Sơ đồ luồng liên kết

Hai pushbutton tách rời trên ribbon (`Support.panel`), nhưng dùng chung đúng hai thứ:

| Thành phần | Đường dẫn |
|------------|-----------|
| T3Lab Assistant | `T3Lab.extension/T3Lab Docs.tab/Support.panel/T3LabAssistant.pushbutton/script.py` |
| LLMs Setting | `T3Lab.extension/T3Lab Docs.tab/Support.panel/Assistant Tools.stack/LLMsSetting.pushbutton/script.py` → `lib/GUI/LLMSettingDialog.py` |
| Router (singleton) | `T3Lab.extension/lib/Intelligence/llm_router.py` |
| Cấu hình bền vững | `T3Lab.extension/lib/config/settings.py` → `%APPDATA%\T3LabAI\settings.json` |

Không có tham chiếu trực tiếp nào khác giữa hai UI này.

---

## D-01 · Sơ đồ liên kết tổng thể

```mermaid
flowchart LR
    subgraph UI["Ribbon — Support panel"]
        A["T3Lab Assistant<br/><small>cửa sổ chat / DockablePane</small>"]
        S["LLMs Setting<br/><small>dialog 5 tab</small>"]
    end

    subgraph CORE["lib — lõi dùng chung"]
        R{{"LLMRouter<br/>singleton"}}
        C[("settings.json<br/>%APPDATA%/T3LabAI")]
    end

    subgraph PROV["Provider adapters"]
        P1["Claude"]
        P2["OpenAI"]
        P3["DeepSeek"]
        P4["Ollama<br/><small>local</small>"]
        P5["LM Studio<br/><small>local</small>"]
    end

    A -- "chat / chat_stream<br/>get_status_instant" --> R
    A -- "nút Settings · chip model<br/>→ ShowDialog" --> S
    S -- "switch_provider / set_model<br/>probe_provider" --> R
    S -- "set_api_key · host · toggle" --> C
    R -- "đọc lúc khởi tạo<br/>ghi khi đổi provider/model" --> C
    P1 & P2 & P3 & P4 & P5 -. "_get_api_key / host" .-> C
    R --> P1 & P2 & P3 & P4 & P5
    S -. "đóng dialog → _refresh_after_settings" .-> A
```

**Ba mối nối:**

1. **Qua RAM — `LLMRouter`**: singleton chung tiến trình Revit. Setting đổi provider là Assistant thấy ngay ở lần chat kế tiếp, không cần restart.
2. **Qua đĩa — `settings.json`**: `api_keys`, `active_provider`, `model_preferences`. Router đọc lại khi dựng singleton (`_restore_settings`).
3. **Qua UI — modal + refresh**: Assistant mở dialog bằng `ShowDialog()`; dialog đóng thì `_refresh_after_settings()` vẽ lại chip model, chip action mode và lời chào.

---

## D-02 · Đổi provider / model — đường đi của một lần bấm

```mermaid
sequenceDiagram
    autonumber
    actor U as Người dùng
    participant D as LLMSettingWindow
    participant S as T3LabAISettings
    participant R as LLMRouter
    participant P as Provider
    participant A as Assistant window

    U->>D: Chọn provider trong combo
    D->>R: switch_provider name
    R->>S: set_active_provider + set_provider_model
    S-->>S: ghi settings.json
    R-->>D: cập nhật cờ active
    D->>R: probe_provider chạy nền
    R->>P: check_health / get_active_model
    P-->>R: available + model
    R-->>D: đổ vào status cache 30s
    D-->>U: chấm trạng thái xanh / xám

    U->>D: Dán API key → Save
    D->>S: set_api_key provider
    D->>P: reload_credentials + validate
    Note over S,P: Provider luôn đọc key tươi từ settings,<br/>không cache trong RAM

    U->>D: Đóng dialog
    D-->>A: ShowDialog trả về
    A->>R: get_status_instant zero-HTTP
    R-->>A: snapshot từ cache / key-presence
    A-->>U: chip model + chip action mode vẽ lại
```

**Chiều ngược lại** — chip model trong khung soạn tin của Assistant mở popup dựng từ
`get_status_instant()` (chỉ liệt kê provider đã sẵn sàng). Chọn một dòng →
`_switch_provider()` → cùng `LLMRouter.switch_provider()` mà dialog dùng, nên lựa chọn
này cũng ghi xuống `settings.json`. Dòng cuối popup "Configure API key & model…" gọi
`_open_llm_settings()`, khép vòng.

---

## D-03 · Một tin nhắn đi qua thang định tuyến

`_route_input()` chạy trên worker thread — LLM chỉ là bậc cuối.

```mermaid
flowchart TD
    IN["Người dùng gửi tin<br/>+ file đính kèm"] --> M{"/memory hoặc<br/>'remember ...'?"}
    M -- có --> MEM["Trả lời từ assistant_memory<br/>không gọi LLM"]
    M -- không --> PDF{"PDF có markup?"}
    PDF -- có --> CA["CommentAgent<br/>đọc annotation"]
    PDF -- không --> RAG["Dựng ngữ cảnh RAG<br/>rag_processor + knowledge index"]
    RAG --> LP{"Learned pattern<br/>khớp?"}
    LP -- có --> EX
    LP -- không --> NLU{"NLU offline<br/>ra intent?"}
    NLU -- có --> EX
    NLU -- không --> DIS["AgentDispatcher<br/>keyword → LLM classify"]
    DIS --> SPEC{"Specialist nào?"}
    SPEC -- knowledge --> KA["KnowledgeAgent<br/>BM25 + embeddings"]
    SPEC -- khác --> LLM

    subgraph LLM["Bậc LLM — qua LLMRouter"]
        direction TB
        NAT{"Provider hỗ trợ<br/>native tools?"}
        NAT -- có --> AL["AgentLoop<br/>tool_schema + MCP registry"]
        NAT -- không --> JS["Legacy JSON-intent loop"]
    end

    AL --> EX["_execute_result<br/>intent + params"]
    JS --> EX
    EX --> LAUNCH["Mở tool T3Lab<br/>BatchOut · ParaSync · CADtoBeam…"]
    EX --> REPLY["Trả lời trong khung chat"]

    NOKEY["Chưa cấu hình provider nào"] -.-> GUIDE["get_setup_guidance_message<br/>chỉ đường sang LLMs Setting"]
    LLM -.- NOKEY
```

Ghi chú: bậc tất định (memory, learned pattern, NLU) chạy được cả khi không có API key.
Bậc fast-context answer đang tắt (`_FAST_CONTEXT_ENABLED = False`).

---

## D-04 · Fallback & trạng thái sức khỏe provider

```mermaid
flowchart TD
    REQ["router.chat / chat_stream"] --> ACT["Provider đang active"]
    ACT -- "trả về text" --> OK["Kết quả"]
    ACT -- "lỗi / None" --> FB{"fallback bật?"}
    FB -- không --> NONE["None → thông báo lỗi"]
    FB -- có --> CHAIN["Duyệt FALLBACK_CHAIN<br/>claude → openai → deepseek → ollama → lmstudio"]
    CHAIN --> HC{"check_health<br/>đạt?"}
    HC -- không --> CHAIN
    HC -- có --> OK

    subgraph ST["Ba mức đọc trạng thái"]
        direction LR
        I["get_status_instant<br/><small>0 HTTP — UI thread</small>"]
        CH["get_status_cached<br/><small>chỉ đọc cache</small>"]
        FULL["get_status<br/><small>probe song song 5 luồng, TTL 30s</small>"]
    end

    I -.-> UI1["Chip model trong chat<br/>Popup chọn model"]
    FULL -.-> UI2["Chấm trạng thái tab Models<br/>nút Test connection"]
    CH -.-> UI1
```

- Probe chạy song song → tổng thời gian bằng provider chậm nhất, không phải tổng.
- Ollama / LM Studio luôn báo "chưa sẵn sàng" trong snapshot instant cho tới khi probe thật (cần port probe).
- UI thread không bao giờ gọi `get_status()` (có mạng) — từng làm treo Revit khi mở LLMs Setting.

---

## settings.json — ai ghi, ai đọc

| Khoá | Ghi bởi | Đọc bởi | Ảnh hưởng |
|------|---------|---------|-----------|
| `api_keys` | Setting · Onboarding | Provider adapters | Quyết định provider có "available" hay không |
| `active_provider` | Setting · chip model · Onboarding | `LLMRouter._restore_settings` | Provider mặc định khi mở Revit lần sau. **Seed lần đầu = `ollama`** (Qwen local, riêng tư + zero API cost); lựa chọn đã lưu vẫn thắng, fallback chain vẫn với tới cloud khi Ollama chưa sẵn sàng |
| `model_preferences` | Setting (Save model) | Router + `get_status_instant` | Model hiển thị trên chip khi chưa probe |
| `Ollama_Host` · `LMStudio_Host` | Setting (tab Models) | Local provider adapters | Địa chỉ engine cục bộ |
| `username` | Setting (tab General) | Assistant — lời chào | `_update_welcome_greeting` |
| `agents.*` · `action_mode` | Setting (tab General) | Dispatcher + Assistant | Multi-agent, LLM classify, quality mode, auto/confirm |
| knowledge dirs | Setting (tab Knowledge) | `knowledge_store` · RAG | Kho tài liệu được index |
| `disabled_skills` | Setting (tab Skills) | `SkillsEngine` | Skill nào hiện trong popup dấu `/` |
| `active_project` | Setting (tab Projects) · chip project | Assistant + context digest | Instruction, memory, file ngữ cảnh của workspace |

Mọi setter đều reload từ đĩa trước khi ghi; file hỏng bị cách ly thành
`settings.corrupt-<stamp>.json` thay vì bị ghi đè — nếu không, một lần bật/tắt toggle
có thể xoá sạch API key.

---

© Copyright by T3Lab
