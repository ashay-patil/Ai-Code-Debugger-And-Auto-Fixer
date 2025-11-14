import os
import sys
import argparse
import json
import re
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import threading
from rich.console import Console
from rich.markdown import Markdown
import google.generativeai as genai
try:
    import tkinter as tk
    from tkinter import filedialog, ttk, messagebox
    from tkinter.scrolledtext import ScrolledText
except Exception:
    tk = None

# =========================
# ⚙ Configuration
# =========================
console = Console()
MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")  #Enter Your Gemini Model name here or use Environment Variable for best pratices
genai.configure(api_key=os.getenv("GEMINI_API_KEY", "...Enter Your Gemini API Key here or Use Environment Variable for best practices..."))

try:
    MODEL = genai.GenerativeModel(MODEL_NAME)
except Exception:
    MODEL = None

SUPPORTED_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".java", ".cpp", ".html", ".css")

# =========================
# 🧩 Utility Functions
# =========================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_logo():
    console.print("""
[bold cyan]
  █████╗ ███████╗      ██████╗   ██████╗  ██████╗  ███████╗    ██████╗  ███████╗██████╗ ██╗   ██╗ ██████╗  ██████╗ ██████╗ ███████
██╔══██╗   ██║       ██╔════╝  ██╔══  ██╗  ██╔══██╗ ██╔════╝   ██╔══██╗ ██╔════╝██╔══██╗██║   ██║██╔════╝ ██╔════╝ ██╔════╝██╔══██╗
███████║   ██║       ██║       ██║    ██║  ██║  ██║ █████╗     ██   ██╔ ███████ ██████╔╝██║   ██║██║  ███╗██║  ███╗█████╗  ██████╔╝
██╔══██║   ██║       ██║       ██║    ██║  ██║  ██║ ██╔══╝     ██╔══██╗ ██╔══╝  ██╔══██╗██║   ██║██║   ██║██║   ██║██╔══╝  ██╔══██╗
██║  ██║ ███████╗     ╚██████╗  ╚██████╔╝ ██████╔╝ ███████╗    ██████╔╝ ███████╗██████╔╝╚██████╔╝╚██████╔╝╚██████╔╝███████╗██║  ██║



[bold magenta]🤖 AI-Based Multi-File Code Debugger + Auto Fixer[/bold magenta]
""")

def get_code_files(directory: str) -> List[str]:
    code_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(SUPPORTED_SUFFIXES):
                path = os.path.join(root, file)
                if any(ignored in Path(path).parts for ignored in ("node_modules", ".git", "__pycache__", "dist", "build")):
                    continue
                code_files.append(path)
    code_files.sort()
    return code_files

def read_file(file_path: str) -> Optional[str]:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        console.print(f"[red]Error reading {file_path}: {e}[/red]")
        return None

def safe_backup(file_path: str):
    try:
        bak = file_path + ".bak"
        if not os.path.exists(bak) and os.path.exists(file_path):
            shutil.copyfile(file_path, bak)
    except Exception as e:
        console.print(f"[yellow]Warning: Could not create backup for {file_path}: {e}[/yellow]")

def clean_code_fences(text: str) -> str:
    """Remove Markdown-style code fences and extra labels like 'Fixed code:'."""
    if not text:
        return ""
    s = text.strip()
    # Remove triple backtick fences
    s = re.sub(r"^```[\w+-]*\n?", "", s)
    s = re.sub(r"\n?```$", "", s)
    # Remove unnecessary labels
    s = re.sub(r'^\s*(Fixed code|Corrected code|Suggested fix)\s*:\s*', '', s, flags=re.IGNORECASE)
    return s.strip() + "\n"

def write_file(file_path: str, new_code: str):
    try:
        safe_backup(file_path)
        cleaned = clean_code_fences(new_code)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(cleaned)
        console.print(f"[green]✅ Updated: {file_path}[/green]")
    except Exception as e:
        console.print(f"[red]❌ Error writing {file_path}: {e}[/red]")

def build_file_section(file_path: str, code: str) -> str:
    suffix = Path(file_path).suffix.lstrip('.') or 'txt'
    return f"### FILE PATH: {file_path}\n```{suffix}\n{code}\n```\n\n"

def chunk_files(files: List[str], file_contents: Dict[str, str], max_chars: int) -> Tuple[List[str], List[List[str]]]:
    """
    Returns tuple of (batches, batch_files) where:
    - batches: List of batch content strings
    - batch_files: List of file lists, each corresponding to a batch
    """
    batches = []
    batch_files = []
    current, current_files, length = [], [], 0
    for f in files:
        section = build_file_section(f, file_contents[f])
        if current and (length + len(section) > max_chars):
            batches.append("\n".join(current))
            batch_files.append(current_files)
            current, current_files, length = [section], [f], len(section)
        else:
            current.append(section)
            current_files.append(f)
            length += len(section)
    if current:
        batches.append("\n".join(current))
        batch_files.append(current_files)
    return batches, batch_files

def generate_content(prompt: str) -> str:
    if MODEL is None:
        raise RuntimeError("Gemini model unavailable. Check API key or model name.")
    try:
        resp = MODEL.generate_content(prompt)
        return resp.text or ""
    except Exception as e:
        return f"Error: {e}"

def review_project(all_code: str, user_prompt: Optional[str]) -> str:
    prompt = f"""
You are an expert software debugger and code reviewer.

Analyze the entire project for defects and risks. Each section below starts with "### FILE PATH:"
and contains the full code for that file. Provide precise, actionable findings and corrected code.

Scope of checks (be thorough and conservative):
- Syntax errors and runtime errors (undefined names, indexing, attribute, import errors)
- Incorrect logic and edge cases (off-by-one, null/None checks, empty input handling)
- Exception handling quality (missing try/except where needed; overbroad excepts; swallowed errors)
- Resource handling (files, network, subprocesses) and leaks; context managers where applicable
- Dependency/import issues (wrong filenames/paths/extensions, missing modules, circular imports)
- API contract mismatches (wrong params/return types, missing awaits, async misuse)
- Type issues and nullability; mutation of shared state; concurrency/async pitfalls
- HTML/CSS/JS linkage mistakes (wrong script/style paths, case sensitivity, extension mismatches)
- Frontend errors (DOM selectors, event binding, missing assets, incorrect MIME/rel attributes)
- Security concerns (eval/exec, command injection, XSS/CSRF risks, hardcoded secrets, plaintext keys)
- Performance hotspots (N+1 queries/polls, excessive loops, unbounded recursion, heavy sync I/O on UI)
- Code quality (duplication, long functions, poor naming, missing docs/comments where non-obvious)
- During api fetching the code should be wrapped in try catch block.If not then the corrected code which you will give should contain that try catch block
Additionally, include three Markdown tables for every file:

Chart 1 - Review Checklist: for the present code and not about the corrected code. The charts should be made for the existing codes
| Review Aspect | Status |
|---|---|
| Variable naming | ✅ / ❌ |
| Hardcoded values/secrets | ✅ / ❌ |
| Code repetition | ✅ / ❌ |
| Modularity | ✅ / ❌ |
| Complexity (high/med/low) | high/med/low |
| Comments & docs | ✅ / ❌ |
| Exception handling present | ✅ / ❌ |
| Dependency/import correctness | ✅ / ❌ |
| Security concerns | ✅ / ❌ |

Chart 2 - API Calls Summary (if any found):
| API Endpoint | Request (sample) | Response (sample) |
|---|---|---|
| (list endpoints or 'None') | | |

Chart 3 - Security Issues / Best Practices:
| Category | Recommendation |
|---|---|
| (e.g. plaintext password storage) | (recommendation) |

User requirement: {user_prompt or 'N/A'}
Important: Fix file import/link mismatches robustly. Example: if the folder has script.js but HTML imports app.js,
change it to script.js; similarly styles.css vs style.css, respecting actual files present. NEVER invent new files
that do not exist—choose the most plausible correct existing path/filename.

Here is the project:
{all_code}

Output format (repeat for all files whether there exists error or not. If no error is present then say that no error and in fixed code place the original code as it is, and if there is error then tell the error and give the corrected code in fixed code):
File: <path>
Error: <short description>
Fixed code:
```<language>
<corrected file code>
```

"""
    return generate_content(prompt)

def auto_fix_project(path: str, review_output: str, files_to_process: List[str], apply_all=False, interactive: bool = True, prompt_func=None, ui_logger=None):
    """
    Auto-fix files based on review output.
    
    Args:
        path: Project directory path
        review_output: Review output for the files being processed
        files_to_process: List of file paths to process (only these files will be fixed)
        apply_all: Whether to apply all fixes automatically
        interactive: Whether to prompt for each fix
        prompt_func: Function to call for user prompts
        ui_logger: Optional UI logger function
    """
    model = genai.GenerativeModel(MODEL_NAME)

    for file_path in files_to_process:
        code = read_file(file_path)
        if not code:
            continue

        fix_prompt = f"""
You are an AI assistant. Using the review below, fix only issues related to this file.

Review:
{review_output}

File: {file_path}
Current Code:
```{Path(file_path).suffix[1:]}
{code}
```

Output the corrected code for this file only.
"""
        try:
            resp = model.generate_content(fix_prompt)
            new_code = resp.text.strip()
            console.rule(f"[bold yellow]🧠 Suggested Fix for {file_path}[/bold yellow]")
            if ui_logger:
                ui_logger(f"\n===== Suggested Fix for {file_path} =====\n")
                ui_logger(new_code[:3000] + ("\n...[truncated]\n" if len(new_code) > 3000 else "\n"))
            else:
                console.print(Markdown(new_code[:3000]))

            if apply_all:
                write_file(file_path, new_code)
            else:
                if interactive:
                    if ui_logger : 
                        apply_change = bool(prompt_func(f"Apply suggested changes to {file_path}?"))
                        if apply_change:
                            write_file(file_path, new_code)
                        else:
                            if ui_logger:
                                ui_logger(f"Skipped: {file_path}\n")
                            else:
                                console.print(f"[yellow]⏩ Skipped: {file_path}[/yellow]")
                    else :
                            choice = input(f"Apply suggested changes to {file_path}? (y/n): ").strip().lower()
                            if choice == 'y':
                                write_file(file_path, new_code)
                            else:
                                console.print(f"[yellow]⏩ Skipped: {file_path}[/yellow]")
                
                else:
                    if ui_logger:
                        ui_logger(f"Skipped (non-interactive): {file_path}\n")
                    
                    else:
                        console.print(f"[yellow]⏩ Skipped (non-interactive): {file_path}[/yellow]")
        except Exception as e:
            console.print(f"[red]Error fixing {file_path}: {e}[/red]")


def save_to_excel(review_output: str, filename: str = "project_review.xlsx"):
    """
    Save Gemini review output into Excel.
    Properly parses Markdown tables and fixed code for each file.
    """
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
        from openpyxl.styles import Font, Alignment
    except ImportError:
        console.print("[yellow]Warning: openpyxl not installed. Cannot save to Excel.[/yellow]")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    # Add raw review text on a second sheet to guarantee content presence
    raw_ws = wb.create_sheet(title="Raw Review")
    raw_ws.cell(row=1, column=1, value="Gemini Review (pre Auto-Fix)")
    raw_ws.cell(row=1, column=1).font = Font(bold=True)
    raw_row = 3
    # Write as lines for readability
    for line in review_output.splitlines():
        raw_ws.cell(row=raw_row, column=1, value=line)
        raw_row += 1
    # Adjust width
    raw_ws.column_dimensions[get_column_letter(1)].width = 120

    wb.save(filename)
    console.print(f"[green]📊 Review saved to Excel file named {filename}[/green]")

def run_pipeline(path: str, export_json: bool, autofix: bool, apply_all: bool, userprompt: Optional[str], max_chars: int, ui_logger=None, excel_filename: str = "project_review.xlsx", interactive: bool = False, prompt_func=None):
    if ui_logger:
        ui_logger("Starting analysis...\n")
    else:
        clear_screen()
        print_logo()

    if not os.path.exists(path):
        msg = f"Invalid path: {path}"
        if ui_logger:
            ui_logger(msg + "\n")
        else:
            console.print(f"[red]{msg}[/red]")
        return

    files = get_code_files(path)
    if not files:
        if ui_logger:
            ui_logger("No source files found!\n")
        else:
            console.print("[red]No source files found![/red]")
        return

    if ui_logger:
        ui_logger(f"Analyzing {len(files)} files...\n")
    else:
        console.rule(f"[bold magenta]🔍 Analyzing {len(files)} files...[/bold magenta]")
    contents = {f: (read_file(f) or "") for f in files}
    batches, batch_files = chunk_files(files, contents, max_chars)

    all_reviews = []
    for i, (batch, batch_file_list) in enumerate(zip(batches, batch_files), 1):
        if ui_logger:
            ui_logger(f"Processing batch {i}/{len(batches)} (Review)...\n")
        else:
            console.print(f"[green]Processing batch {i}/{len(batches)} (Review)...[/green]")
        output = review_project(batch, userprompt)
        all_reviews.append(output)
        
        # Print review for this batch immediately
        if ui_logger:
            ui_logger(f"\n==== Review for batch {i}/{len(batches)} ====\n")
            # Pass is_markdown=True to render markdown properly
            review_content = output[:20000] + ("\n...[truncated]\n" if len(output) > 20000 else "\n")
            ui_logger(review_content, is_markdown=True)
        else:
            console.rule(f"[bold blue]🧩 Review for batch {i}/{len(batches)}[/bold blue]")
            console.print(Markdown(output[:20000]))
        
        # Auto-fix immediately after reviewing this batch
        if autofix:
            if ui_logger:
                ui_logger(f"Processing batch {i}/{len(batches)} (Auto-Fix)...\n")
            else:
                console.rule(f"[bold green]🔧 Auto-Fix for batch {i}/{len(batches)}[/bold green]")
            auto_fix_project(path, output, batch_file_list, apply_all=apply_all, interactive=True, prompt_func=prompt_func, ui_logger=ui_logger)

    review_output = "\n\n".join(all_reviews)
    save_to_excel(review_output, filename=excel_filename)

    if export_json:
        with open('project_review.json', 'w', encoding='utf-8') as f:
            json.dump({"review": review_output}, f, indent=2)
        if ui_logger:
            ui_logger("Review saved to project_review.json\n")
        else:
            console.print("[green]📦 Review saved to project_review.json[/green]")

    if ui_logger:
        ui_logger("\nDone.\n")
    else:
        console.rule("[bold green]✅ Done[/bold green]")
    return review_output

def launch_ui():
    if tk is None:
        console.print("[red]Tkinter is not available in this environment. Please run via CLI.[/red]")
        sys.exit(1)

    app = tk.Tk()
    app.title("AI Code Debugger + Auto Fixer (Gemini)")
    app.geometry("900x700")
    try:
        app.iconbitmap(default="")  # ignore if not available
    except Exception:
        pass
    app.configure(bg="#f5f7fb")

    path_var = tk.StringVar()
    json_var = tk.BooleanVar(value=False)
    autofix_var = tk.BooleanVar(value=False)
    apply_all_var = tk.BooleanVar(value=False)
    userprompt_var = tk.StringVar()
    maxchars_var = tk.IntVar(value=200_000)
    is_running = tk.BooleanVar(value=False)

    # Header bar with subtle animation
    header = tk.Frame(app, bg="#2b2d42", height=52)
    header.pack(fill=tk.X, side=tk.TOP)
    title_lbl = tk.Label(header, text="AI Code Debugger + Auto Fixer (Gemini)", fg="white", bg="#2b2d42", font=("Segoe UI", 12, "bold"))
    title_lbl.pack(side=tk.LEFT, padx=12, pady=10)
    status_lbl = tk.Label(header, text="Idle", fg="#a8dadc", bg="#2b2d42", font=("Segoe UI", 10))
    status_lbl.pack(side=tk.RIGHT, padx=12)

    # Root content frame (ttk with padding looks cleaner)
    frm = ttk.Frame(app, padding=10)
    frm.pack(fill=tk.BOTH, expand=True)

    path_row = ttk.Frame(frm)
    path_row.pack(fill=tk.X, pady=5)
    ttk.Label(path_row, text="Project folder:").pack(side=tk.LEFT)
    path_entry = ttk.Entry(path_row, textvariable=path_var)
    path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
    def browse():
        d = filedialog.askdirectory()
        if d:
            path_var.set(d)
    browse_btn = ttk.Button(path_row, text="Browse...", command=browse)
    browse_btn.pack(side=tk.LEFT)

    opts_row1 = ttk.Frame(frm)
    opts_row1.pack(fill=tk.X, pady=5)
    json_chk = ttk.Checkbutton(opts_row1, text="Export review as JSON", variable=json_var)
    json_chk.pack(side=tk.LEFT, padx=5)
    autofix_chk = ttk.Checkbutton(opts_row1, text="Auto-fix", variable=autofix_var)
    autofix_chk.pack(side=tk.LEFT, padx=5)
    apply_all_chk = ttk.Checkbutton(opts_row1, text="Apply all fixes automatically", variable=apply_all_var)
    apply_all_chk.pack(side=tk.LEFT, padx=5)

    prompt_row = ttk.Frame(frm)
    prompt_row.pack(fill=tk.X, pady=5)
    ttk.Label(prompt_row, text="User requirement (optional):").pack(side=tk.LEFT)
    prompt_entry = ttk.Entry(prompt_row, textvariable=userprompt_var)
    prompt_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

    max_row = ttk.Frame(frm)
    max_row.pack(fill=tk.X, pady=5)
    ttk.Label(max_row, text="Max chars per batch:").pack(side=tk.LEFT)
    max_entry = ttk.Entry(max_row, textvariable=maxchars_var, width=12)
    max_entry.pack(side=tk.LEFT, padx=5)

    out_label = ttk.Label(frm, text="Output:")
    out_label.pack(anchor="w", pady=(10, 0))
    # Footer button row anchored to bottom so it never gets pushed off-screen
    btn_row = ttk.Frame(frm)
    btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=10)

    out_container = ttk.Frame(frm)
    out_container.pack(fill=tk.BOTH, expand=True)
    out_text = ScrolledText(
        out_container,
        height=25,
        wrap=tk.WORD,
        bg="#f8fafc",                # soft background
        fg="#0f172a",               # near-black text
        insertbackground="#0f172a", # cursor color
        borderwidth=1,
        relief="solid"
    )
    try:
        out_text.configure(font=("Consolas", 10), padx=14, pady=12, spacing1=4, spacing3=4)
    except Exception:
        pass
    # Define color tags
    try:
        out_text.tag_configure("section", foreground="#1d4ed8", font=("Consolas", 10, "bold"))
        out_text.tag_configure("info", foreground="#2563eb")
        out_text.tag_configure("success", foreground="#059669")
        out_text.tag_configure("warning", foreground="#d97706")
        out_text.tag_configure("error", foreground="#dc2626")
        out_text.tag_configure("muted", foreground="#475569")
        out_text.tag_configure("code", background="#eef2f7")
    except Exception:
        pass
    out_text.pack(fill=tk.BOTH, expand=True)

    # Create progress bar with fixed-width placeholder to prevent layout shift
    progress_placeholder = ttk.Label(btn_row, width=28)  # Fixed width placeholder
    progress_placeholder.pack(side=tk.LEFT, padx=10)
    
    progress = ttk.Progressbar(btn_row, mode="indeterminate", length=220)
    
    run_btn = ttk.Button(btn_row, text="Run")
    run_btn.pack(side=tk.LEFT)
    def clear_output():
        out_text.delete("1.0", tk.END)
    ttk.Button(btn_row, text="Clear Output", command=clear_output).pack(side=tk.LEFT, padx=5)

    def render_markdown(text: str, start_pos: str = "1.0"):
        """Render markdown text with appropriate formatting tags."""
        lines = text.split('\n')
        i = 0
        in_code_block = False
        code_block_lines = []

        while i < len(lines):
            line = lines[i]

            # Toggle code block on triple backticks
            if line.strip().startswith("```"):
                if not in_code_block:
                    in_code_block = True
                    code_block_lines = []
                else:
                    # closing fence, flush code block
                    code_text = '\n'.join(code_block_lines)
                    out_text.insert(tk.END, code_text + '\n', ("code",))
                    out_text.insert(tk.END, '\n')
                    in_code_block = False
                i += 1
                continue

            if in_code_block:
                code_block_lines.append(line)
                i += 1
                continue

            # Headers
            if line.startswith('###'):
                header_text = line.lstrip('#').strip()
                out_text.insert(tk.END, header_text + '\n', ("section",))
                i += 1
                continue
            elif line.startswith('##'):
                header_text = line.lstrip('#').strip()
                out_text.insert(tk.END, header_text + '\n', ("section",))
                i += 1
                continue
            elif line.startswith('#'):
                header_text = line.lstrip('#').strip()
                out_text.insert(tk.END, header_text + '\n', ("section",))
                i += 1
                continue

            # Tables - detect header line followed by separator line with dashes
            if '|' in line and i + 1 < len(lines) and re.match(r'^\s*\|?\s*[-:\s|]+\s*\|?\s*$', lines[i + 1]):
                # Table header
                cols = [c.strip() for c in line.split('|')[1:-1]]
                if cols:
                    header_row = '| ' + ' | '.join(cols) + ' |\n'
                    out_text.insert(tk.END, header_row, ("info",))
                    separator = '|' + '|'.join([' --- '] * len(cols)) + '|\n'
                    out_text.insert(tk.END, separator, ("muted",))
                i += 2  # skip header and separator
                # Table rows
                while i < len(lines) and '|' in lines[i]:
                    row_cols = [c.strip() for c in lines[i].split('|')[1:-1]]
                    while len(row_cols) < len(cols):
                        row_cols.append('')
                    row_cols = row_cols[:len(cols)]
                    row_text = '| ' + ' | '.join(row_cols) + ' |\n'
                    out_text.insert(tk.END, row_text)
                    i += 1
                out_text.insert(tk.END, '\n')
                continue

            # Inline code using single backticks
            if '`' in line:
                parts = re.split(r'(`[^`]+`)', line)
                for part in parts:
                    if part.startswith('`') and part.endswith('`') and len(part) > 2:
                        code_part = part.strip('`')
                        out_text.insert(tk.END, code_part, ("code",))
                    else:
                        out_text.insert(tk.END, part)
                out_text.insert(tk.END, '\n')
                i += 1
                continue

            # Regular text
            out_text.insert(tk.END, line + '\n')
            i += 1

    def safe_log(msg: str, tag: str = None, is_markdown: bool = False):
        def append():
            applied_tag = tag
            low = msg.lower()
            if applied_tag is None:
                if "error" in low or "❌" in msg or "failed" in low:
                    applied_tag = "error"
                elif low.startswith("starting") or "processing batch" in low:
                    applied_tag = "info"
                elif "suggested fix" in low or "gemini debugging output" in low or "starting auto fix" in low:
                    applied_tag = "section"
                elif "saved" in low or "✅" in msg or "done" in low:
                    applied_tag = "success"

            if is_markdown:
                # Render markdown in a readable way using your renderer
                try:
                    render_markdown(msg, "1.0")
                except Exception:
                    # fallback: insert raw if markdown rendering fails
                    out_text.insert(tk.END, msg + ("\n" if not msg.endswith("\n") else ""), (applied_tag,))
            else:
                msg_to_insert = msg if msg.endswith("\n") else msg + "\n"
                if applied_tag:
                    out_text.insert(tk.END, msg_to_insert, (applied_tag,))
                else:
                    out_text.insert(tk.END, msg_to_insert)

            out_text.see(tk.END)
            out_text.update_idletasks()  # Force refresh
        app.after(0, append)

    def ui_yes_no(question: str) -> bool:
        result_holder = {"ans": False}
        gate = threading.Event()
        def ask():
            try:
                ans = messagebox.askyesno("Apply Fix?", question, parent=app)
            except Exception:
                ans = False
            result_holder["ans"] = bool(ans)
            gate.set()
        app.after(0, ask)
        gate.wait()
        return result_holder["ans"]

    # Simple header pulse animation while running
    def pulse_header(step: int = 0):
        if not is_running.get():
            header.configure(bg="#2b2d42")
            title_lbl.configure(bg="#2b2d42")
            status_lbl.configure(bg="#2b2d42")
            return
        # Two-tone pulse
        colors = ["#2b2d42", "#31344f"]
        c = colors[step % len(colors)]
        header.configure(bg=c)
        title_lbl.configure(bg=c)
        status_lbl.configure(bg=c)
        app.after(300, lambda: pulse_header(step + 1))

    def set_running(running: bool):
        is_running.set(running)
        state = "disabled" if running else "normal"
        for w in (path_entry, browse_btn, json_chk, autofix_chk, apply_all_chk, prompt_entry, max_entry, run_btn):
            try:
                w.configure(state=state)
            except Exception:
                pass
        if running:
            status_lbl.configure(text="Running...", fg="#ffd166")
            run_btn.configure(text="Running...")
            progress_placeholder.pack_forget()  # Hide placeholder
            progress.pack(side=tk.LEFT, padx=10)  # Show progress bar
            progress.start(12)
            pulse_header(0)
        else:
            status_lbl.configure(text="Idle", fg="#a8dadc")
            run_btn.configure(text="Run")
            try:
                progress.stop()
            except Exception:
                pass
            progress.pack_forget()  # Hide progress bar
            progress_placeholder.pack(side=tk.LEFT, padx=10)  # Show placeholder to maintain layout

    def on_run():
        folder = path_var.get().strip()
        export_json = bool(json_var.get())
        autofix = bool(autofix_var.get())
        apply_all = bool(apply_all_var.get())
        userprompt = userprompt_var.get().strip() or None
        try:
            max_chars = int(maxchars_var.get())
        except Exception:
            max_chars = 200_000
            maxchars_var.set(max_chars)

        if not folder:
            safe_log("Please select a project folder.\n")
            return

        set_running(True)

        def worker():
            try:
                interactive = bool(autofix and not apply_all)
                prompt_func = ui_yes_no if interactive else None
                run_pipeline(folder, export_json, autofix, apply_all, userprompt, max_chars, ui_logger=safe_log, interactive=interactive, prompt_func=prompt_func)
            except Exception as e:
                safe_log(f"Error: {e}\n")
            finally:
                app.after(0, lambda: set_running(False))

        threading.Thread(target=worker, daemon=True).start()

    run_btn.configure(command=on_run)

    app.mainloop()

def main():
    # Launch UI if no CLI arguments are provided
    if len(sys.argv) == 1:
        launch_ui()
        return

    clear_screen()
    print_logo()

    parser = argparse.ArgumentParser(description='AI Multi-File Code Debugger + Auto Fixer + User Prompt')
    parser.add_argument('path', help='Path to project directory')
    parser.add_argument('--json', action='store_true', help='Export review as JSON')
    parser.add_argument('--autofix', action='store_true', help='Enable interactive auto-fix')
    parser.add_argument('--apply-all', action='store_true', help='Apply all fixes automatically')
    parser.add_argument('--userprompt', '-p', type=str, help='Custom user requirement prompt')
    parser.add_argument('--max-chars', type=int, default=200_000, help='Max characters per Gemini batch')
    args = parser.parse_args()

    run_pipeline(args.path, args.json, args.autofix, args.apply_all, args.userprompt, args.max_chars, ui_logger=None)

if __name__ == "__main__":
    main()