<<<<<<< Updated upstream
_
=======
"""
Docs-only Telegram Bot
- Answers ONLY using your local PDFs/Excels/CSVs.
- Uses TF-IDF retrieval + GPT with strict grounding.
- Commands:
    /start  -> info
    /reload -> (re)index documents in DOCS_DIR
"""

import os
from typing import Tuple, List, Dict

# ---- .env loader (reads TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, DOCS_DIR) ----
from dotenv import load_dotenv

# ---- Reading documents ----
import pandas as pd            # Excel/CSV handling
import fitz                   # PyMuPDF for PDF text extraction

# ---- Retrieval (TF-IDF + cosine similarity) ----
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ---- Telegram bot SDK ----
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ---- OpenAI client ----
from openai import OpenAI

# =========================
# 0) ENV & GLOBALS
# =========================
load_dotenv()  # loads variables from .env in current folder

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # your Telegram bot token
DOCS_DIR = os.getenv("DOCS_DIR", "./documents")   # folder for local docs

client = OpenAI()  # reads OPENAI_API_KEY from .env automatically

# Retrieval configuration (you can tweak these later)
CHUNK_SIZE = 1000          # characters per chunk when splitting long text
CHUNK_OVERLAP = 200        # overlap between chunks to keep context continuity
TOP_K = 6                  # how many top chunks to send to GPT
MIN_SIM_THRESHOLD = 0.18   # if best similarity < this, we refuse to answer
NGRAM_RANGE = (1, 2)       # use unigrams + bigrams in TF-IDF

# In-memory index storage
INDEX_READY = False
CHUNKS: List[str] = []         # the text chunks
CHUNK_META: List[Dict] = []    # metadata for each chunk (file path, page/sheet info, etc.)
VECTORIZER: TfidfVectorizer = None
MATRIX = None                  # TF-IDF matrix of all chunks

# =========================
# 1) FILE DISCOVERY & READERS
# =========================
def discover_files(root: str) -> list[str]:
    """
    Recursively find PDF / Excel / CSV files under a folder.
    """
    files = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            lo = fn.lower()
            if lo.endswith(".pdf") or lo.endswith(".xlsx") or lo.endswith(".xls") or lo.endswith(".csv"):
                files.append(os.path.join(dirpath, fn))
    return sorted(files)

def read_pdf(path: str) -> tuple[str, dict]:
    """
    Extract raw text from a PDF using PyMuPDF.
    Returns (text, meta).
    """
    try:
        doc = fitz.open(path)
        pages = [page.get_text("text") for page in doc]
        return "\n".join(pages), {"type": "pdf", "pages": len(pages)}
    except Exception as e:
        print(f"[WARN] PDF read error {path}: {e}")
        return "", {"type": "pdf", "pages": 0}

def read_excel(path: str) -> tuple[str, dict]:
    """
    Read all sheets from an Excel file and convert them to a text table.
    """
    try:
        xls = pd.ExcelFile(path)
        parts = []
        for sheet in xls.sheet_names:
            df = pd.read_excel(path, sheet_name=sheet)
            parts.append(f"=== Sheet: {sheet} ===\n{df.to_string(index=False)}")
        return "\n\n".join(parts), {"type": "excel", "sheets": len(xls.sheet_names)}
    except Exception as e:
        print(f"[WARN] Excel read error {path}: {e}")
        return "", {"type": "excel", "sheets": 0}

def read_csv(path: str) -> tuple[str, dict]:
    """
    Read a CSV and convert it to a text table.
    """
    try:
        df = pd.read_csv(path)
        return df.to_string(index=False), {"type": "csv", "rows": len(df)}
    except Exception as e:
        print(f"[WARN] CSV read error {path}: {e}")
        return "", {"type": "csv", "rows": 0}

# =========================
# 2) CHUNKING & INDEX BUILD
# =========================
def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split long text into overlapping windows:
    e.g., [0:1000], [800:1800], [1600:2600], ...
    Overlap helps keep context continuity.
    """
    if not text:
        return []
    chunks = []
    start = 0
    n = len(text)
    step = max(1, size - overlap)
    while start < n:
        end = min(n, start + size)
        # collapse extra whitespace to reduce tokens
        chunk = " ".join(text[start:end].split())
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks

def build_index() -> str:
    """
    1) Discover files → 2) Read them → 3) Chunk → 4) Fit TF-IDF vectorizer
    """
    global INDEX_READY, CHUNKS, CHUNK_META, VECTORIZER, MATRIX
    CHUNKS = []
    CHUNK_META = []

    files = discover_files(DOCS_DIR)
    if not files:
        INDEX_READY = False
        return f"⚠️ No PDF/Excel/CSV files found under: {DOCS_DIR}"

    # Read every file and generate chunks
    for path in files:
        lo = path.lower()
        if lo.endswith(".pdf"):
            text, meta = read_pdf(path)
        elif lo.endswith(".xlsx") or lo.endswith(".xls"):
            text, meta = read_excel(path)
        elif lo.endswith(".csv"):
            text, meta = read_csv(path)
        else:
            continue

        for i, ch in enumerate(chunk_text(text)):
            CHUNKS.append(ch)
            CHUNK_META.append({"source": path, "chunk_index": i, "meta": meta})

    if not CHUNKS:
        INDEX_READY = False
        return "⚠️ Found files but couldn’t read any content."

    # Fit TF-IDF over all chunks (bag-of-words with unigrams+bigrams)
    VECTORIZER = TfidfVectorizer(ngram_range=NGRAM_RANGE, stop_words="english")
    MATRIX = VECTORIZER.fit_transform(CHUNKS)
    INDEX_READY = True
    return f"✅ Indexed {len(files)} file(s), {len(CHUNKS)} chunk(s)."

# =========================
# 3) RETRIEVAL + GPT (STRICT)
# =========================
SYSTEM_RULES = (
    "You are a strict retrieval-augmented assistant. "
    "You must ONLY answer using the provided context. "
    "If the answer is not fully supported by the context, reply exactly with: "
    "\"I don't know based on the provided documents.\" "
    "Do not use prior knowledge. Do not guess."
)

def retrieve(query: str, top_k: int = TOP_K):
    """
    Convert the query to TF-IDF space and compute cosine similarity vs all chunks.
    Return top_k chunks + their meta + best similarity score.
    """
    if not INDEX_READY:
        return [], [], 0.0
    q_vec = VECTORIZER.transform([query])
    sims = cosine_similarity(q_vec, MATRIX).ravel()
    order = sims.argsort()[::-1][:top_k]
    top_chunks = [CHUNKS[i] for i in order]
    top_meta = [CHUNK_META[i] for i in order]
    best = float(sims[order[0]]) if len(order) else 0.0
    return top_chunks, top_meta, best

def answer_from_docs(question: str) -> str:
    """
    - Retrieve chunks for the user's question.
    - If similarity is too low, refuse with a fixed sentence.
    - Otherwise, pass chunks as 'Context' to GPT with strict system rules.
    """
    chunks, meta, best = retrieve(question, TOP_K)

    if (not chunks) or (best < MIN_SIM_THRESHOLD):
        return "I don't know based on the provided documents."

    # Join top chunks into a single context block (truncate for safety)
    context = ("\n\n---\n\n".join(chunks))[:12000]

    messages = [
        {"role": "system", "content": SYSTEM_RULES},
        {"role": "user",
         "content": f"Context:\n{context}\n\nQuestion: {question}\n\nAnswer using ONLY the context:"}
    ]

    # Call GPT; 4o-mini is fast & cheap. Use gpt-4o for higher quality.
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.0,  # deterministic
    )
    return resp.choices[0].message.content.strip()

# =========================
# 4) TELEGRAM HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start -> short help message.
    """
    await update.message.reply_text(
        "📚 I answer ONLY from local PDF/Excel/CSV files.\n"
        "Use /reload after you add or update documents."
    )

async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reload -> (re)build the index from DOCS_DIR
    """
    await update.message.chat.send_action(action="typing")
    msg = build_index()
    await update.message.reply_text(msg)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Normal text messages:
    - Ensure index exists (build on first use)
    - Get a strict, docs-grounded answer
    """
    if not INDEX_READY:
        await update.message.reply_text("⏳ Building index (first time)...")
        msg = build_index()
        await update.message.reply_text(msg)
        if not INDEX_READY:
            return

    user_q = (update.message.text or "").strip()
    if not user_q:
        await update.message.reply_text("Please send a text question.")
        return

    await update.message.chat.send_action(action="typing")
    try:
        ans = answer_from_docs(user_q)
        await update.message.reply_text(ans)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

def main():
    """
    Wire up handlers and start long polling.
    """
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reload", reload_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
# This is the entry point for the Telegram bot
# It will start the bot and listen for commands/messages
# Make sure to set TELEGRAM_BOT_TOKEN and DOCS_DIR in your .env file
<<<<<<< Updated upstream
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
