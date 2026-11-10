# DeNotive

An evidence-board notebook: **projects → boards → evidence**, tied together with red string.

***Very much an entirely vibe-coded program. I had no direct involvement in the code writing for this besides the original prompt. Even the icon is AI generated through claude Opus 5.5*** I had an idea for a note taking app that i would want that may or may not be a good idea. My style of note-taking is very sporadic, and traditional note taking apps like onenote end up doing more harm than good. This gives me the ability to collate and link sporadic ideas more easily, while also having a built-in history retaining feature for scripts if they are drag and dropped into the evidence board so you know exactly what version corresponds to what data. 

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
- **Formatting text** in a note, sticky or shape: double-click to type and a small toolbar appears above it. Select
  some words, then use it or the keys: **Ctrl+B** bold, **Ctrl+I** italic, **Ctrl+U** underline, **Ctrl+Shift+X**
  strikethrough, **Ctrl+Shift+8** bulleted list, **Ctrl+Shift+7** numbered list. Press again to turn it off. With
  nothing selected it applies to what you type next. Enter continues a list; Enter on an empty line ends it. When
  you're not typing, the same buttons in the side panel format the whole note.
- **Superscript / subscript** while typing in a note, sticky or shape: **Ctrl+=** raises the selected text (10⁻³),
  **Ctrl+-** lowers it (H₂O); press again to set it back. With nothing selected it applies to what you type next.
  When you're not typing, Ctrl+= / Ctrl+- zoom as before.
- Hold **Shift** while drawing an arrow/line (or dragging one of its ends) to keep it perfectly straight:
  across (0° / 180°) or up and down (90° / 270°), whichever is nearer.
- **Select several things** with Shift-click or Ctrl-click (click again to drop one), or drag a box around them.
  Every selected item gets its own outline, so you can see exactly what you've picked; move, align, restyle or
  delete them together.
- **Scale several at once**: with several things selected, drag the round grip at the bottom-right of any of them.
  The whole selection grows or shrinks together about its top-left corner, keeping its layout: positions, sizes,
  line lengths and the text size of notes, stickies and shapes all scale. One Ctrl+Z puts it back.
- Navigate: wheel zooms, right-drag / middle-drag / Space-drag pans, **Ctrl+0** fits, **M** minimap, **I** side panel.
- Ctrl+Z / Ctrl+Y undo and redo, Ctrl+D duplicate, Delete removes (deleted things go to Trash).
- **Linked copies**: right-click an evidence card or file → *Linked copy here* or *Linked copy on another board*, or
  copy it and press **Ctrl+Shift+V** on any board. The copies share one history, so editing any of them (or updating
  a file's snapshot) updates them all. Put the same evidence in two frames or on two boards without tying strings.
  Linked cards show a blue chain badge with how many places they're in. The side panel lists the places (click
  to jump there) and has **Unlink this one** to give a copy its own history again. Deleting one copy leaves the
  others intact, even after the Trash is emptied.

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
- **Open in new window**: right-click a board in the sidebar (or a project, for its map), or use the board's ⋯
  menu. It opens in a second DeNotive window, on your other screen if you have one; or snap the two windows side
  by side (Win+←/→) for a split screen. Each window shows what the other changes within a couple of seconds.
  Each window keeps its own undo history, and the main window is the one that remembers where you left off.

## Co-Investigator (optional AI)
Nothing is sent anywhere until you ask. Set it up in **Settings → AI settings…**, choosing a model separately for
the Co-Investigator and for note drafting:
- **Claude via the Claude desktop app** — uses your normal Claude sign-in, no API key (tools switched off).
- **ChatGPT via the Codex command-line tool** — uses your ChatGPT plan, no API key. Install it once from PowerShell
  (`irm https://chatgpt.com/codex/install.ps1 | iex`), then press **Sign in with ChatGPT…** in AI settings. It runs
  read-only with its command tool, web search and your personal Codex config switched off. API keys in your
  environment aren't passed to it, and AI settings warns you if Codex itself is signed in with a key. Answers appear
  when they're finished rather than word by word.
- **Anthropic, OpenAI or Google Gemini API keys** — keys are stored encrypted for your Windows account.
- **Ollama** — local models; nothing leaves the PC. Vision models (llama3.2-vision, llava, gemma) can see slides.
- **Any OpenAI-compatible server** (LM Studio, OpenRouter, Groq…).
- **Copy & paste** — no API at all: the question and case file go to the clipboard, the chatbot site opens, and you
  paste the reply back.

Picking a model: type one, or press **List models** to open a list of the models that provider offers (installed
ones, for Ollama) and pick one. A short guide under the box says which is which — for Claude: **haiku** is the
smallest and fastest (great for notes), **sonnet** is balanced, **opus** is the most capable (best for the
Co-Investigator, but uses more of your plan's limits); for Codex, **default** lets Codex choose for your plan.
**Test** sends a tiny request to check it works; **Sign in to Claude…** / **Sign in with ChatGPT…** opens the
sign-in if you aren't logged in. The model chip at the top of the
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

- **Smart guides**, like PowerPoint: drag anything (shapes, pictures, text, lines and arrows, or a whole
  selection) and its edges and centre snap to the edges and centres of everything else on the slide, however far
  away (move a line until its end lines up with the lines below it), and to the page edges and middle. Pink
  guide lines show what it's lined up with. Resizing, dragging a line's end and drawing new shapes and lines
  snap the same way. Hold **Alt** to move freely without snapping.
- **Line ends stick to objects**: draw a line or arrow from (or drag its end onto) a corner, an edge's middle or
  the centre of a shape, picture or text box. Little dots show the spots, and a stuck end turns green. Move,
  resize or turn that object and the end follows, stretching the line. Dragging the line itself away, or
  Alt-dragging an end, lets go. In the side panel, *Ends stick to objects* switches it off for that line, and
  **Attach…** next to Start or End pins that end to whatever object you click next, right where the end is now
  (**Detach** lets go). **Ctrl+drag** a stuck (green) end to keep it stopped short of its spot: the tip stays that
  far away but always aims straight at the spot (say, the middle of a word), wherever the word or the other end
  moves. A green dot and a dashed line show the spot. Ctrl+drag back onto it to remove the gap.
- **Zoom-in callouts**: pick the **zoom tool** (Z, or *Zoom into part…* on a picture) and drag over the part of a
  picture you want to magnify. A box appears beside it showing that part enlarged at the picture's full
  resolution, with an outline on the picture and lines joining the two. Or use **Punch & Zoom**: draw any shape
  (circle, star…) over the picture, then right-click it → *Punch & Zoom*. The shape becomes the outline and the
  box takes its shape and colour. Drag the outline to magnify a different part; its corner dots resize it. The
  side panel sets the magnification, shape, outline colour and whether the joining lines show. The callout
  moves with its picture and goes with it when the picture is deleted.
- **Arrow keys** move the selection 1 pixel (**Shift**+arrow: 10).
- **Copy into PowerPoint, Word or Excel**: select things and press Ctrl+C, then paste in the other app. They
  arrive as real, editable PowerPoint objects at their true size and layout: shapes stay shapes with their text,
  text boxes keep their formatting (bold, lists, sub/superscript, columns), pictures stay pictures, lines and
  arrows stay lines and ink becomes freeform drawing. Zoom-in callouts and tables come over as pictures. Other
  apps (Paint, chat apps) get a picture.
- **Formatting keys**: Ctrl+B, Ctrl+I, Ctrl+U, **Ctrl+Shift+X** strikethrough, **Ctrl+Shift+8** bullets,
  **Ctrl+Shift+7** numbers, the same as on the board.
- **Justify and columns**: the Text panel has **Justify** (both edges straight) next to the other alignments, and
  **Columns** with the gap between them for text boxes. The text flows down one column into the next, and the
  columns stay balanced. While you type in the box it shows as one column so the cursor behaves; it goes back
  to columns when you click away. PowerPoint export and copying keep real columns.
- **Superscript / subscript**: **Ctrl+=** raises and **Ctrl+-** lowers the selected text (or, with a text box
  selected but not being edited, all of it); press again for normal. The **x²** / **x₂** buttons in the Text panel
  do the same. They carry through to PDF and PowerPoint, and the Co-Investigator reads them as 10⁻³, H₂O, x^(n).
  With no text selected, Ctrl+= / Ctrl+- zoom the slide.
- **Group**: select several things and press **Ctrl+G** (or right-click → Group). Clicking any member selects the
  whole group, which moves, aligns and resizes as one (drag its handles to scale everything in it).
  **Ctrl+Shift+G** ungroups. Grouped items show ⧉ in the Layers list.
- **Pages**: need more room? Click **+ Add page** under the page (or **Page** in the toolbar, or right-click →
  *Add page below*). Pages stack top to bottom with a small gap; scroll between them, or use the arrows /
  Page Up / Page Down. Things snap to every page's edges. Right-click a page → *Delete page*, or the bin next to
  the page counter (Ctrl+Z brings it back). On the board the evidence card shows all its pages stacked.

Shortcuts: Ctrl+S save as new version, Ctrl+Shift+S update the current version, Esc back to the board.

## Finding things
- **Ctrl+K** searches everything; **Ctrl+F** searches what you're looking at (board, or the open evidence).
  Words, "exact phrases", string labels, notes and the contents of attached files (code, text, CSV, notebooks,
  Word/PowerPoint/Excel) are all indexed, including older versions.
- **Timeline** (Ctrl+T) lists evidence newest-first for everything, a project or a board — grouped by date,
  project or board, sorted by created or updated, filtered to the last day/week/month.

## Exporting
Board **Export** menu (or right-click a board/project in the sidebar): every evidence card becomes one slide
(an evidence with several pages becomes one slide per page), optionally after an overview page of the whole board.
- **PDF** — vector, text stays selectable.
- **PowerPoint** — editable text boxes, pictures (with crops), shapes, arrows and ink; notes and string
  connections go into the speaker notes. Or choose "Pictures" for an exact-look deck.
- **Print** — with preview.
