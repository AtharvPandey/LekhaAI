# HisaabBot — AI Accountant-in-a-Box

WhatsApp-first GST compliance agent for Indian micro-businesses.

**Send invoice photo on WhatsApp → AI extracts all data → Monthly GST summary → Filing-ready report for CA**

## Quick Start

```bash
# 1. Clone and enter
cd hisaab-bot

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Mac/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy env file and fill in your keys
cp .env.example .env
# Edit .env with your API keys

# 5. Run the app
uvicorn app.main:app --reload --port 8000

# 6. Expose via ngrok (for WhatsApp webhook)
ngrok http 8000
```

## Project Structure

```
hisaab-bot/
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings & environment vars
│   ├── api/
│   │   ├── webhooks.py      # WhatsApp webhook handlers
│   │   └── dashboard.py     # CA dashboard API (Phase 4)
│   ├── core/
│   │   ├── ocr.py           # Invoice OCR pipeline (Gemini Vision)
│   │   ├── gst_engine.py    # GST calculation & logic
│   │   ├── reconciliation.py # GSTR-2B matching (Phase 3)
│   │   └── report_gen.py    # PDF report generator (Phase 2)
│   ├── services/
│   │   ├── whatsapp.py      # WhatsApp Cloud API client
│   │   ├── gemini.py        # Gemini API wrapper
│   │   └── conversation.py  # Conversation state machine
│   ├── models/
│   │   ├── database.py      # Database connection & setup
│   │   ├── schemas.py       # Pydantic models
│   │   └── tables.py        # SQL table definitions
│   └── utils/
│       ├── validators.py    # GSTIN, HSN, tax rate validation
│       ├── image_prep.py    # Image preprocessing
│       └── helpers.py       # Common utilities
├── tests/
├── scripts/
│   └── seed_hsn.py          # Seed HSN code database
├── docs/
│   └── implementation_plan.md
├── .env.example
├── requirements.txt
└── README.md
```

## Implementation Phases

| Phase | Timeline | What | Status |
|-------|----------|------|--------|
| Phase 0 | Week 1-2 | Foundation + GST learning | 🔨 Building |
| Phase 1 | Week 3-5 | Invoice OCR MVP on WhatsApp | ⏳ Pending |
| Phase 2 | Week 6-8 | GST logic + monthly reports | ⏳ Pending |
| Phase 3 | Week 9-11 | GSTR-2B reconciliation | ⏳ Pending |
| Phase 4 | Week 12-14 | CA web dashboard | ⏳ Pending |
| Phase 5 | Week 15-16 | Launch + first customers | ⏳ Pending |
