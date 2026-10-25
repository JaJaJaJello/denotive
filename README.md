# DeNotive

An evidence-board notebook: **projects → boards → evidence**, tied together with red string.

**Run:** `dist\DeNotive\DeNotive.exe` (keep the `_internal` folder next to it).
**Rebuild:** `build.bat` · **Run from source:** `.venv\Scripts\python.exe run.py`

Your data lives in one folder (default `Documents\DeNotive Library`): a SQLite database plus an
`assets` folder of images and file snapshots. A dated copy of the database is kept in `backups` every day.
Change or back up the library from the ⚙ button in the sidebar.

## Boards
- **Right-click** the board → *Add evidence* (or press **E**, or the red **Evidence** button) to open the slide editor.
- **Drag from a red pin** to another card to tie a string. Double-click a string (or press Enter) to label it.
  Colour, dashed (tentative), direction arrows, thickness and slack are in the side panel.
- **Double-click** a card to edit it. Saving stacks a new version on top; older versions peek out
  underneath. *History* shows every version and lets you restore one or edit from it.
- **Drop files** (scripts, data, PDFs, programs) onto the board, or use **Attach**. Files up to the snapshot
  limit (Settings, default 50 MB) are copied into the library so you always have the exact version you used;
  *Update snapshot* stacks a new version when the original changes. Images dropped on a board become evidence
  (hold Shift to attach them as files instead).
- **Ctrl+V** pastes a screenshot as new evidence, text as a sticky note, or copied cards.
- Tools: **V** select · **H** pan · **S** string · **T** text · **N** sticky · **R** shape · **L** arrow ·
  **F** frame (dragging a frame carries everything inside it).
- **Select several things** with Shift-click or Ctrl-click (click again to drop one), or drag a box around them.
  Every selected item gets its own outline, so you can see exactly what you've picked; move, align, restyle or
  delete them together.
- Navigate: wheel zooms, right-drag / middle-drag / Space-drag pans, **Ctrl+0** fits, **M** minimap, **I** side panel.
- Ctrl+Z / Ctrl+Y undo and redo, Ctrl+D duplicate, Delete removes (deleted things go to Trash).

## Project maps and board links
Projects are links of boards; boards are links of evidence.
- **Click a project** in the sidebar (click its chevron to fold it) to open its **project map**: every board
  in the project appears as a case file showing its latest evidence. Tie labelled strings between boards just
  like evidence, and double-click a board to open it. The map keeps itself up to date as boards are added,
  moved, deleted or restored.
- **Link any board onto any other board** (even across projects): drag a board from the sidebar onto the
  canvas, or right-click → *Link a board here*. Tie evidence to the link card; double-click it to jump there.
- **Clicking a board in the sidebar** zooms to fit everything on it. If you ever pan or zoom off into empty
  space, a *back to the board* button appears at the top.
- Exporting a project starts with its map.

## Co-Investigator (optional AI)
Nothing is sent anywhere until you ask. Set it up in **Settings → AI settings…**, choosing a model separately for
the Co-Investigator and for note drafting:
- **Claude via the Claude desktop app** — uses your normal Claude sign-in, no API key (tools switched off).
- **Anthropic, OpenAI or Google Gemini API keys** — keys are stored encrypted for your Windows account.
- **Ollama** — local models; nothing leaves the PC. Vision models (llama3.2-vision, llava, gemma) can see slides.
- **Any OpenAI-compatible server** (LM Studio, OpenRouter, Groq…).
- **Copy & paste** — no API at all: the question and case file go to the clipboard, the chatbot site opens, and you
  paste the reply back.

Picking a model: type one, or press **List models** to open a list of the models that provider offers (installed
ones, for Ollama) and pick one. A short guide under the box says which is which — for Claude: **haiku** is the
smallest and fastest (great for notes), **sonnet** is balanced, **opus** is the most capable (best for the
Co-Investigator, but uses more of your plan's limits). **Test** sends a tiny request to check it works; for Claude,
**Sign in to Claude…** opens the sign-in if the app isn't logged in. The model chip at the top of the
Co-Investigator panel shows what's in use — click it to change it.

Using it:
- The **Co-Investigator** button (magnifier, top right of a board) opens a side panel. Ask anything, or pick a
  suggested question ("What evidence is missing?", "What should I do next?", "Find weak links"…). Choose whether it
  looks at this board, the whole project or just your selection. Answers cite items like [E3] — click to jump there.
  **Pin to board** turns an answer into a lavender sticky or slide tagged CO-INVESTIGATOR. Conversations are kept
  per board.
- **Draft notes** (sparkle, above the notes in the evidence editor) writes notes from the slide image, its text and
  what it's tied to, and suggests a title if there isn't one. One Ctrl+Z removes the draft.
- **Personality**: in AI settings the Co-Investigator can answer in character. Pick Normal, a 1920s private
  investigator trying to crack the case, a skeptical peer reviewer, an encouraging lab mentor, Sherlock Holmes, a
  film-noir narrator, a patient tutor or a pirate captain — or type any job title or short description. It changes
  the voice, never the facts or the [E3] citations, and each answer shows who's talking under its header (e.g.
  *as a 1920s private investigator…*). Bigger models commit to the act more (opus fully; haiku adds a light touch).
  Drafted notes stay plain unless you tick *Use the Co-Investigator's personality for drafted notes too*.
- **Case file**: board ⋯ menu → *Copy case file* / *Save case file…* (or right-click a project → Export → Case file
  for AI) gives a Markdown summary of everything — evidence text and notes, files, strings and labels, frames — to
  paste into any AI or keep as a record.

## Evidence editor
Text boxes (rich text, lists, tables), code blocks with highlighting, pictures (insert, paste, drag in,
**Snip** a region of the screen), crop, 12 shapes, lines and arrows, pen and highlighter ink, eraser,
layers, lock, align/distribute, smart guides, rotate, opacity, shadows and several page sizes.
**Double-click** an empty spot on the page to drop a text box there and start typing. Shift-click or Ctrl-click
selects several things at once, each with its own outline.
The notes pane under the slide is searchable and is exported as speaker notes.
Shortcuts: Ctrl+S save as new version, Ctrl+Shift+S update the current version, Esc back to the board.

## Finding things
- **Ctrl+K** searches everything; **Ctrl+F** searches what you're looking at (board, or the open evidence).
  Words, "exact phrases", string labels, notes and the contents of attached files (code, text, CSV, notebooks,
  Word/PowerPoint/Excel) are all indexed, including older versions.
- **Timeline** (Ctrl+T) lists evidence newest-first for everything, a project or a board — grouped by date,
  project or board, sorted by created or updated, filtered to the last day/week/month.

## Exporting
Board **Export** menu (or right-click a board/project in the sidebar): every evidence card becomes one slide,
optionally after an overview page of the whole board.
- **PDF** — vector, text stays selectable.
- **PowerPoint** — editable text boxes, pictures (with crops), shapes, arrows and ink; notes and string
  connections go into the speaker notes. Or choose "Pictures" for an exact-look deck.
- **Print** — with preview.
