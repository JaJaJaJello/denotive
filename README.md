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

## Evidence editor
Text boxes (rich text, lists, tables), code blocks with highlighting, pictures (insert, paste, drag in,
**Snip** a region of the screen), crop, 12 shapes, lines and arrows, pen and highlighter ink, eraser,
layers, lock, align/distribute, smart guides, rotate, opacity, shadows and several page sizes.
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
